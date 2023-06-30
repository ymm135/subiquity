# Copyright 2023 Canonical, Ltd.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import asyncio
import logging
from typing import List, Optional

from subiquitycore.context import with_context

from subiquity.common.apidef import API
from subiquity.common.types import OEMResponse
from subiquity.models.oem import OEMMetaPkg
from subiquity.server.apt import OverlayCleanupError
from subiquity.server.controller import SubiquityController
from subiquity.server.curtin import run_curtin_command
from subiquity.server.kernel import flavor_to_pkgname
from subiquity.server.types import InstallerChannels
from subiquity.server.ubuntu_drivers import (
    CommandNotFoundError,
    get_ubuntu_drivers_interface,
    )

log = logging.getLogger('subiquity.server.controllers.oem')


class OEMController(SubiquityController):

    endpoint = API.oem

    model_name = "oem"

    def __init__(self, app) -> None:
        super().__init__(app)
        # At this point, the source variant has not been selected but it only
        # has an impact if we're listing drivers, not OEM metapackages.
        self.ubuntu_drivers = get_ubuntu_drivers_interface(self.app)

        self.load_metapkgs_task: Optional[asyncio.Task] = None
        self.kernel_configured_event = asyncio.Event()

    def start(self) -> None:
        self._wait_apt = asyncio.Event()
        self.app.hub.subscribe(
            InstallerChannels.APT_CONFIGURED,
            self._wait_apt.set)
        self.app.hub.subscribe(
            (InstallerChannels.CONFIGURED, "kernel"),
            self.kernel_configured_event.set)

        async def list_and_mark_configured() -> None:
            await self.load_metapackages_list()
            await self.ensure_no_kernel_conflict()
            await self.configured()

        self.load_metapkgs_task = asyncio.create_task(
                list_and_mark_configured())

    async def wants_oem_kernel(self, pkgname: str,
                               *, context, overlay) -> bool:
        """ For a given package, tell whether it wants the OEM or the default
        kernel flavor. We look for the Ubuntu-Oem-Kernel-Flavour attribute in
        the package meta-data. If the attribute is present and has the value
        "default", then return False. Otherwise, return True. """
        result = await run_curtin_command(
            self.app, context,
            "in-target", "-t", overlay.mountpoint, "--",
            "apt-cache", "show", pkgname,
            capture=True, private_mounts=True)
        for line in result.stdout.decode("utf-8").splitlines():
            if not line.startswith("Ubuntu-Oem-Kernel-Flavour:"):
                continue

            flavor = line.split("=", maxsplit=1)[1].strip()
            if flavor == "default":
                return False
            elif flavor == "oem":
                return True
            else:
                log.warning("%s wants unexpected kernel flavor: %s",
                            pkgname, flavor)
                return True

        log.warning("%s has no Ubuntu-Oem-Kernel-Flavour", pkgname)
        return True

    @with_context()
    async def load_metapackages_list(self, context) -> None:
        with context.child("wait_apt"):
            await self._wait_apt.wait()

        # Skip looking for OEM meta-packages if we are running ubuntu-server.
        # OEM meta-packages expect the default kernel flavor to be HWE (which
        # is only true for ubuntu-desktop).
        if self.app.base_model.source.current.variant == "server":
            log.debug("not listing OEM meta-packages since we are installing"
                      " ubuntu-server")
            self.model.metapkgs = []
            return

        apt = self.app.controllers.Mirror.final_apt_configurer
        try:
            async with apt.overlay() as d:
                try:
                    # Make sure ubuntu-drivers is available.
                    await self.ubuntu_drivers.ensure_cmd_exists(d.mountpoint)
                except CommandNotFoundError:
                    self.model.metapkgs = []
                else:
                    metapkgs: List[str] = await self.ubuntu_drivers.list_oem(
                        root_dir=d.mountpoint,
                        context=context)
                    self.model.metapkgs = [
                        OEMMetaPkg(
                            name=name,
                            wants_oem_kernel=await self.wants_oem_kernel(
                                name, context=context, overlay=d),
                        ) for name in metapkgs]

        except OverlayCleanupError:
            log.exception("Failed to cleanup overlay. Continuing anyway.")

        for pkg in self.model.metapkgs:
            if pkg.wants_oem_kernel:
                kernel_model = self.app.base_model.kernel
                kernel_model.metapkg_name_override = flavor_to_pkgname(
                        pkg.name, dry_run=self.app.opts.dry_run)

                log.debug("overriding kernel flavor because of OEM")

        log.debug("OEM meta-packages to install: %s", self.model.metapkgs)

    async def ensure_no_kernel_conflict(self) -> None:
        kernel_model = self.app.base_model.kernel

        await self.kernel_configured_event.wait()

        if self.model.metapkgs and kernel_model.explicitly_requested:
            # TODO
            # This should be a dialog or something rather than the content of
            # an exception, really. But this is a simple way to print out
            # something in autoinstall.
            msg = _("""\
A specific kernel flavor was requested but it cannot be satistified when \
installing on certified hardware.
You should either disable the installation of OEM meta-packages using the \
following autoinstall snippet or let the installer decide which kernel to
install.
  oem:
    install: false
""")
            raise RuntimeError(msg)

    @with_context()
    async def apply_autoinstall_config(self, context) -> None:
        await self.load_metapkgs_task
        await self.ensure_no_kernel_conflict()

    async def GET(self, wait: bool = False) -> OEMResponse:
        if wait:
            await asyncio.shield(self.load_metapkgs_task)
        if self.model.metapkgs is None:
            metapkgs = None
        else:
            metapkgs = [pkg.name for pkg in self.model.metapkgs]
        return OEMResponse(metapackages=metapkgs)
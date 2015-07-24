# Copyright 2015 Canonical, Ltd.
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

from urwid import (Text, Filler, WidgetWrap,
                   ListBox, BoxAdapter)
from subiquity.view import ViewPolicy
from subiquity.ui.utils import Color, Padding


class ProgressOutput(WidgetWrap):
    def __init__(self, txt):
        self.txt = Text(txt)
        flr = Filler(Color.info_minor(self.txt),
                     valign="bottom")
        super().__init__(BoxAdapter(flr, height=20))

    def split_text(self):
        return self.txt.text.splitlines()

    def set_text(self, data):
        data = data.decode("utf8")
        last_ten_lines = data[-1500:]
        lines = self.split_text() + last_ten_lines.splitlines()
        out = "\n".join(lines[-20:])
        self.txt.set_text(out)


class ProgressView(ViewPolicy):
    def __init__(self, signal, output_w):
        """
        :param output_w: Filler widget to display updated status text
        """
        self.body = [
            Padding.center_79(output_w)
        ]
        super().__init__(ListBox(self.body))

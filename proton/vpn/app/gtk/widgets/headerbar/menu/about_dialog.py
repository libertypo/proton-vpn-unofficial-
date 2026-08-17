"""
Module for the about dialog.


Copyright (c) 2026 Proton AG

This file is part of Proton VPN.

Proton VPN is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

Proton VPN is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with ProtonVPN.  If not, see <https://www.gnu.org/licenses/>.
"""

from pathlib import Path

from gi.repository import Gdk, Gio, Gtk

from proton.vpn.app.gtk.assets import icons
from proton.vpn.app.gtk.i18n import tr as _


class AboutDialog(Gtk.AboutDialog):
    """This widget will display general information about this application"""

    TITLE = "About"
    PROGRAM_NAME = "Proton VPN Linux Client"
    VERSION = "Unofficial version"
    COPYRIGHT = "Original work: Proton AG. Community modifications included."
    LICENSE = Gtk.License.GPL_3_0
    AUTHORS = [
        "Community Maintainers (unofficial build)",
        "Original project: Proton AG",
    ]
    COMMENTS = "This build includes community modifications and is not endorsed by Proton AG."

    def __init__(self):
        super().__init__()
        self.set_title(_(self.TITLE))
        self.set_program_name(_(self.PROGRAM_NAME))
        self.set_version(_(self.VERSION))
        self.set_copyright(_(self.COPYRIGHT))
        self.set_license_type(self.LICENSE)
        self.set_authors([_(author) for author in self.AUTHORS])
        self.set_comments(_(self.COMMENTS))
        self._set_icon()

    def _set_icon(self):
        texture = Gdk.Texture.new_from_file(Gio.File.new_for_path(str(icons.ICONS_PATH / Path("proton-vpn-sign.svg"))))
        self.set_logo(texture)

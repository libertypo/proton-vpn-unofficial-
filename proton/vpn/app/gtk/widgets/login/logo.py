"""
Copyright (c) 2023 Proton AG

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

from gi.repository import Gdk, Gio, Gtk

from proton.vpn.app.gtk.assets import icons


def _set_picture_from_icon_file(picture: Gtk.Picture, filename: str):
    gfile = Gio.File.new_for_path(str(icons.ICONS_PATH / filename))
    texture = Gdk.Texture.new_from_file(gfile)
    picture.set_paintable(texture)


class ProtonVPNLogo(Gtk.Picture):
    """Proton VPN logo shown in the login widget."""

    LOGO_WIDTH = 320

    def __init__(self):
        super().__init__()
        self.set_name("login-logo")
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        self.set_can_shrink(False)

        self.set_size_request(self.LOGO_WIDTH, -1)
        _set_picture_from_icon_file(self, "proton-vpn-logo.svg")


class TwoFactorAuthProtonVPNLogo(Gtk.Picture):
    """Proton VPN logo shown in the login widget."""

    LOGO_WIDTH = 200

    def __init__(self):
        super().__init__()
        self.set_name("two-factor-auth-vpn-logo")
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.START)
        self.set_can_shrink(False)

        self.set_size_request(self.LOGO_WIDTH, -1)
        _set_picture_from_icon_file(self, "proton-vpn-logo.svg")


class SecurityKeyLogo(Gtk.Picture):
    """Proton VPN logo shown in the login widget."""

    LOGO_WIDTH = 400

    def __init__(self):
        super().__init__()
        self.set_name("security-key-logo")
        self.set_hexpand(False)
        self.set_vexpand(False)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

        self.set_size_request(self.LOGO_WIDTH, -1)
        _set_picture_from_icon_file(self, "security-key.svg")

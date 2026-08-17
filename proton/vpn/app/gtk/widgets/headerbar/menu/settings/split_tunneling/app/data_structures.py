"""
Copyright (c) 2025 Proton AG

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

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Optional

from gi.repository import Gdk, GdkPixbuf, GLib, GObject, Gtk

from proton.vpn.app.gtk.assets import icons
from proton.vpn.app.gtk.utils.safe_signal_connect import safe_signal_connect
from proton.vpn.app.gtk.widgets.headerbar.menu.settings.common import SettingName

ICON_SIZE_IN_PX = 16


@dataclass(eq=True, frozen=True)
class AppData:  # pylint: disable=missing-class-docstring
    name: str
    executable: str
    icon_name: Optional[str]

    def to_dict(self) -> dict[str, object]:
        """Convert dataclass to dict

        Returns:
            dict[str, object]
        """
        return asdict(self)

    @staticmethod
    def from_dict(data: dict[str, object]) -> AppData:
        """Build object based on dict.

        Args:
            data (dict[str, object])

        Returns:
            AppData
        """
        name = data.get("name")
        executable = data.get("executable")
        icon_name = data.get("icon_name")

        if not isinstance(name, str) or not isinstance(executable, str):
            raise ValueError("Invalid app data")
        if icon_name is not None and not isinstance(icon_name, str):
            raise ValueError("Invalid app icon data")

        return AppData(
            name=name,
            executable=executable,
            icon_name=icon_name,
        )


def _get_missing_icon_pixbuff() -> GdkPixbuf.Pixbuf:
    return icons.get(Path("no-app-icon.svg"))


def _image_from_pixbuf(pixbuff: GdkPixbuf.Pixbuf, gtk: ModuleType = Gtk) -> Gtk.Image:
    success, encoded = pixbuff.save_to_bufferv("png", [], [])
    if not success:
        raise RuntimeError("Failed to encode icon pixbuf")
    texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(encoded))
    return gtk.Image.new_from_paintable(texture)


def get_icon(img_path: Optional[str], gtk: ModuleType = Gtk) -> Gtk.Image:
    """Returns a Gtk.Image based either on the app path image or else
    uses a default one.
    """
    pixbuff = None

    # If it starts with / then we've received a path to an image
    if img_path:
        try:
            if img_path.startswith("/"):
                pixbuff = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                    filename=img_path, width=ICON_SIZE_IN_PX, height=-1, preserve_aspect_ratio=True
                )
            else:
                display = Gdk.Display.get_default()
                if display is None:
                    raise ValueError("Display is not available")
                theme = Gtk.IconTheme.get_for_display(display)
                icon_paintable = theme.lookup_icon(
                    icon_name=img_path,
                    fallbacks=None,
                    size=ICON_SIZE_IN_PX,
                    scale=1,
                    direction=Gtk.TextDirection.NONE,
                    flags=Gtk.IconLookupFlags(0),
                )
                if icon_paintable:
                    return gtk.Image.new_from_paintable(icon_paintable)
        except GLib.Error:
            pass

    if not pixbuff:
        pixbuff = _get_missing_icon_pixbuff()

    return _image_from_pixbuf(pixbuff, gtk)


class AppRowWithCheckbox(Gtk.Grid):
    """App row used to display when selecting apps to be split tunneled."""

    def __init__(self, app_data: AppData, checked: bool, gtk: ModuleType = Gtk):
        super().__init__()
        self.set_column_spacing(10)
        self.set_name(app_data.executable)

        self.app_data = app_data
        self._checked = checked
        self.gtk = gtk
        self._check_button: Optional[Gtk.CheckButton] = None

    @staticmethod
    def build(app_data: AppData, checked: bool) -> AppRowWithCheckbox:
        """Method to automate building the object

        Args:
            app_data (AppData):
            checked (bool)

        Returns:
            AppRowWithCheckbox
        """
        app_row = AppRowWithCheckbox(app_data, checked)
        app_row.build_ui()

        return app_row

    def build_ui(self):
        """Build the UI.
        It's still the parents responsibility to display it.
        """
        icon = get_icon(self.app_data.icon_name, self.gtk)
        label = SettingName(self.app_data.name)

        self._check_button = self.gtk.CheckButton.new()
        self._check_button.set_active(self._checked)

        self.attach(self._check_button, 0, 0, 1, 1)
        self.attach(icon, 1, 0, 1, 1)
        self.attach(label, 2, 0, 1, 1)

    @property
    def checked(self) -> bool:
        """Only available when built with `AppRow.build_with_remove_button()`.

        Returns:
            bool
        """
        check_button = self._check_button
        if check_button is None:
            raise RuntimeError("Check button is not initialized")
        return check_button.get_active()

    def _set_check(self, val: bool):
        """Mainly for testing purposes and not for public API.

        Args:
            val (bool)
        """
        check_button = self._check_button
        if check_button is None:
            raise RuntimeError("Check button is not initialized")
        check_button.set_active(val)


class AppRowWithRemoveButton(Gtk.Grid):
    """_summary_"""

    def __init__(self, app_data: AppData, gtk: ModuleType = Gtk):
        super().__init__()
        self.set_column_spacing(10)
        self.set_name(app_data.executable)

        self.app_data = app_data
        self.gtk = gtk
        self._remove_button = None

    @staticmethod
    def build(app_data: AppData) -> AppRowWithRemoveButton:
        """Method to automate building the object

        Args:
            app_data (AppData)

        Returns:
            AppRowWithRemoveButton
        """
        app_row = AppRowWithRemoveButton(app_data)
        app_row.build_ui()

        return app_row

    def build_ui(self):
        """Build the UI.
        It's still the parents responsibility to display it.
        """
        icon = get_icon(self.app_data.icon_name, self.gtk)
        label = SettingName(self.app_data.name)
        self._remove_button = Gtk.Button.new_from_icon_name("edit-delete-symbolic")
        safe_signal_connect(self._remove_button, "clicked", self._signal_remove_app)

        self.attach(icon, 0, 0, 1, 1)
        self.attach(label, 1, 0, 1, 1)
        self.attach(self._remove_button, 2, 0, 1, 1)

    def _signal_remove_app(self, _: Gtk.Button):
        self.emit("remove-app")

    @GObject.Signal(name="remove-app")
    def remove_app(self):
        """
        Signal emitted after the user clicks on remove button.

        Since this object returns itself, we don't need to pass any arguments
        as we can easily access the `app` property.
        """

    def _click_on_remove_button(self):
        """Mainly for testing purposes and not for public API."""
        self._remove_button.emit("clicked")

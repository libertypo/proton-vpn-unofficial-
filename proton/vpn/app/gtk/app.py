"""
This module defines the main App class.


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

import sys
from typing import Optional

from gi.repository import Gdk, Gio, GLib, GObject, Gtk

from proton.vpn import logging
from proton.vpn.app.gtk.assets.style import STYLE_PATH
from proton.vpn.app.gtk.config import (
    THEME_PREFERENCE_DARK,
    THEME_PREFERENCE_LIGHT,
    THEME_PREFERENCE_SYSTEM,
)
from proton.vpn.app.gtk.controller import Controller
from proton.vpn.app.gtk.i18n import should_use_rtl_layout
from proton.vpn.app.gtk.util import APPLICATION_ID, log_proton_package_versions
from proton.vpn.app.gtk.widgets.main.main_window import MainWindow
from proton.vpn.app.gtk.widgets.main.tray_indicator import TrayIndicator, TrayIndicatorNotSupported

logger = logging.getLogger(__name__)


class App(Gtk.Application):
    """
    Proton VPN GTK application.

    It inherits a set of common app functionality from Gtk.Application:
    https://docs.gtk.org/gtk3/class.Application.html.

    For example:
     - It guarantees that only one instance of the application is
       allowed (new app instances exit immediately if there is already
       an instance running).
     - It manages the windows associated to the application. The application
       exits automatically when the last one is closed.
     - It allows desktop shell integration by exporting actions and menus.
    """

    APP_LIGHT_THEME_NAME = "Adwaita"
    APP_DARK_THEME_NAME = "Adwaita-dark"

    def __init__(self, controller: Controller):
        super().__init__(application_id=APPLICATION_ID)
        logger.info("self=%r", self, category="APP", event="PROCESS_START")
        log_proton_package_versions()
        self._controller = controller
        self.window: Optional[MainWindow] = None
        self._tray_indicator = None
        self._start_minimized_from_cli = False
        self._css_provider: Optional[Gtk.CssProvider] = None
        self._gtk_settings: Optional[Gtk.Settings] = None
        self._gtk_theme_name_handler_id: Optional[int] = None
        self._gtk_interface_color_scheme_handler_id: Optional[int] = None
        self._interface_settings: Optional[Gio.Settings] = None
        self._interface_settings_handler_ids: list[int] = []
        self._updating_gtk_theme_name = False
        self.add_options()

    def do_startup(self):  # pylint: disable=arguments-differ
        """Default GTK method.

        Runs at application startup, to load
        any necessary UI elements.
        """
        Gtk.Application.do_startup(self)
        self._css_provider = Gtk.CssProvider()

        settings = Gtk.Settings.get_default()
        self._gtk_settings = settings
        if settings is not None:
            self._gtk_theme_name_handler_id = settings.connect(
                "notify::gtk-theme-name", self._on_theme_settings_changed
            )
            if hasattr(settings.props, "gtk_interface_color_scheme"):
                self._gtk_interface_color_scheme_handler_id = settings.connect(
                    "notify::gtk-interface-color-scheme", self._on_theme_settings_changed
                )

        self._connect_interface_theme_watchers()
        self.apply_theme_preference()

        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, self._css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _on_theme_settings_changed(self, *_):
        if self._updating_gtk_theme_name:
            return

        preference = self._controller.get_app_configuration().theme_preference
        if preference != THEME_PREFERENCE_SYSTEM:
            return

        self._reload_theme_css()

    def _connect_interface_theme_watchers(self):
        try:
            self._interface_settings = Gio.Settings.new("org.gnome.desktop.interface")
        except (GLib.Error, TypeError, ValueError):
            # pragma: no cover - schema availability depends on host
            self._interface_settings = None
            logger.warning(
                "Unable to initialize org.gnome.desktop.interface settings; falling back to GTK theme detection"
            )
            return

        for key in ("color-scheme", "gtk-theme"):
            handler_id = self._interface_settings.connect(f"changed::{key}", self._on_theme_settings_changed)
            self._interface_settings_handler_ids.append(handler_id)

    def apply_theme_preference(self):
        """Apply app-level theme preference by selecting the proper stylesheet."""
        settings = Gtk.Settings.get_default()
        if settings is not None:
            preference = self._controller.get_app_configuration().theme_preference
            self._apply_gtk_theme_name_override(settings, preference)

        self._reload_theme_css()

    def _apply_gtk_theme_name_override(self, settings: Gtk.Settings, preference: str):
        if preference == THEME_PREFERENCE_DARK:
            target_theme_name = self.APP_DARK_THEME_NAME
        elif preference == THEME_PREFERENCE_LIGHT:
            target_theme_name = self.APP_LIGHT_THEME_NAME
        else:
            target_theme_name = (
                self.APP_DARK_THEME_NAME if self._uses_dark_theme_for_system() else self.APP_LIGHT_THEME_NAME
            )

        if not target_theme_name:
            return

        current_theme_name = str(settings.props.gtk_theme_name or "")
        if current_theme_name == target_theme_name:
            return

        self._updating_gtk_theme_name = True
        try:
            settings.props.gtk_theme_name = target_theme_name
        finally:
            self._updating_gtk_theme_name = False

    def _reload_theme_css(self):
        if self._css_provider is None:
            return

        use_dark_theme = self._uses_dark_theme()
        stylesheet_name = "main_dark.css" if use_dark_theme else "main_light.css"
        try:
            self._css_provider.load_from_path(str(STYLE_PATH / stylesheet_name))
        except GLib.Error:
            logger.exception("Unable to load stylesheet %s", stylesheet_name)
            return

        for app_window in self.get_windows():
            app_window.queue_draw()

    def _uses_dark_theme(self) -> bool:
        preference = self._controller.get_app_configuration().theme_preference
        if preference == THEME_PREFERENCE_DARK:
            return True
        if preference == THEME_PREFERENCE_LIGHT:
            return False

        return self._uses_dark_theme_for_system()

    def _uses_dark_theme_for_system(self) -> bool:
        """Detect system dark mode without relying on app-specific CSS choice."""
        interface_color_scheme = self._get_interface_color_scheme_from_gsettings()
        if interface_color_scheme is not None:
            return interface_color_scheme == "dark"

        interface_theme_name = self._get_interface_theme_name_from_gsettings()
        if interface_theme_name:
            normalized_theme_name = interface_theme_name.lower()
            return normalized_theme_name.endswith("-dark") or normalized_theme_name.endswith(":dark")

        settings = Gtk.Settings.get_default()
        if settings is None:
            return False

        color_scheme = self._get_interface_color_scheme(settings)
        if color_scheme is not None:
            return color_scheme == "dark"

        theme_name = (settings.props.gtk_theme_name or "").lower()
        if theme_name:
            return theme_name.endswith("-dark") or theme_name.endswith(":dark")

        return True

    def _get_interface_color_scheme(self, settings: Gtk.Settings) -> Optional[str]:
        color_scheme = getattr(settings.props, "gtk_interface_color_scheme", None)
        if color_scheme is None:
            return None

        normalized = str(color_scheme).lower()
        if normalized.endswith("dark"):
            return "dark"
        if normalized.endswith("light"):
            return "light"

        return None

    def _get_interface_color_scheme_from_gsettings(self) -> Optional[str]:
        if self._interface_settings is None:
            return None

        raw_value = self._interface_settings.get_string("color-scheme")
        normalized = str(raw_value).lower()
        if normalized.endswith("dark"):
            return "dark"
        if normalized.endswith("light"):
            return "light"

        return None

    def _get_interface_theme_name_from_gsettings(self) -> Optional[str]:
        if self._interface_settings is None:
            return None

        theme_name = self._interface_settings.get_string("gtk-theme")
        if not theme_name:
            return None

        return str(theme_name)

    def do_shutdown(self):  # pylint: disable=arguments-differ
        self._disconnect_theme_watchers()
        Gtk.Application.do_shutdown(self)

    def _disconnect_theme_watchers(self):
        if self._gtk_settings is not None:
            if self._gtk_theme_name_handler_id is not None:
                self._gtk_settings.disconnect(self._gtk_theme_name_handler_id)
                self._gtk_theme_name_handler_id = None
            if self._gtk_interface_color_scheme_handler_id is not None:
                self._gtk_settings.disconnect(self._gtk_interface_color_scheme_handler_id)
                self._gtk_interface_color_scheme_handler_id = None
            self._gtk_settings = None

        if self._interface_settings is not None:
            for handler_id in self._interface_settings_handler_ids:
                self._interface_settings.disconnect(handler_id)
            self._interface_settings_handler_ids = []
            self._interface_settings = None

    def do_activate(self):  # pylint: disable=W0221
        """
        Method called by Gtk.Application when the default first window should
        be shown to the user.
        """
        if not self.window:
            self.window = MainWindow(self, self._controller)
            self.window.set_direction(Gtk.TextDirection.RTL if should_use_rtl_layout() else Gtk.TextDirection.LTR)
            # Windows are associated with the application like this.
            # When the last one is closed, the application shuts down.
            self.add_window(self.window)
            # The behaviour of the button to close the window is configured
            # depending on whether the tray indicator is shown or not.
            self.window.configure_close_button_behaviour(tray_indicator_enabled=(self.tray_indicator is not None))
            self.window.set_visible(True)

        self.window.present()
        self.emit("app-ready")

    def do_handle_local_options(self, options: GLib.VariantDict):  # noqa: E501 pylint: disable=arguments-differ
        """
        Handles the options defined in add_options
        Returns:
            Any negative number: Start as usual
            Zero: Stop without error
            Any positive number: Stop with the number as error code
        """
        if options.contains("version"):
            sys.stdout.write(f"{self._controller.app_version}\n")
            return 0

        if options.contains("start-minimized"):
            self._start_minimized_from_cli = True

        return -1

    @property
    def error_dialog(self) -> Gtk.MessageDialog:
        """
        Gives access to currently opened error message dialogs. This method
        was made available for testing purposes.
        """
        window = self.window
        if window is None:
            raise RuntimeError("Main window is not initialized")
        return window.main_widget.notifications.error_dialog

    @GObject.Signal(name="app-ready")
    def app_ready(self):
        """Signal emitted when the app is ready for interaction."""
        if self._start_app_minimized and self.tray_indicator:
            self.window.set_visible(False)

    @property
    def _start_app_minimized(self) -> bool:
        return self._start_minimized_from_cli or self._controller.get_app_configuration().start_app_minimized

    def add_options(self):
        """Adds the --start-minimized and --version command line options"""
        self.add_main_option(
            "start-minimized", 0, GLib.OptionFlags(0), GLib.OptionArg.NONE, "Start minimized in the system tray"
        )

        self.add_main_option(
            "version", ord("v"), GLib.OptionFlags(0), GLib.OptionArg.NONE, "Display the application's version"
        )

    @property
    def tray_indicator(self):
        """Gives access to the tray indicator if it's installed and enabled."""
        if self._tray_indicator:
            return self._tray_indicator

        try:
            tray_indicator = TrayIndicator(self._controller)
            tray_indicator.setup(self.window)
        except TrayIndicatorNotSupported as excp:
            logger.warning(str(excp))
        else:
            self._tray_indicator = tray_indicator

        return self._tray_indicator

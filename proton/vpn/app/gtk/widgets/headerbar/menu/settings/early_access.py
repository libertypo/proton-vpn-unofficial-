"""
Early access handler module.


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

import os
import re
import shlex
import shutil
import subprocess  # nosec B404 # nosemgrep: gitlab.bandit.B404
from collections.abc import Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Optional, Tuple, cast

import distro
from gi.repository import GLib, Gtk, Pango

from proton.vpn import logging
from proton.vpn.app.gtk.controller import Controller
from proton.vpn.app.gtk.utils.safe_signal_connect import safe_signal_connect
from proton.vpn.app.gtk.widgets.headerbar.menu.settings.common import ToggleWidget
from proton.vpn.app.gtk.widgets.main.loading_widget import Spinner

logger = logging.getLogger(__name__)

_PYTEST_ENV_VAR = "PYTEST_CURRENT_TEST"

_VALID_PACKAGE_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.:+-]+$")
_VALID_COMMAND_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_./:+-]+$")

_SENSITIVE_OUTPUT_PATTERN = re.compile(
    r"(?i)("
    r"authorization\s*:\s*bearer\s+[^\s,;]+"
    r"|token\s*[:=]\s*[^\s,;]+"
    r"|password\s*[:=]\s*[^\s,;]+"
    r"|secret\s*[:=]\s*[^\s,;]+"
    r"|api[_-]?key\s*[:=]\s*[^\s,;]+"
    r")"
)


def _sanitize_output_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return _SENSITIVE_OUTPUT_PATTERN.sub("[REDACTED]", value)


COMPATIBLE_DISTRIBUTIONS = distro.like().split()
COMPATIBLE_DISTRIBUTIONS.append(distro.id())


def _validate_package_name(value: str, parameter_name: str) -> str:
    if not value:
        raise ValueError(f"{parameter_name} must be a non-empty string")
    if "\x00" in value or "\n" in value or "\r" in value or "\t" in value:
        raise ValueError(f"{parameter_name} contains unsupported control characters")
    if not _VALID_PACKAGE_NAME_PATTERN.fullmatch(value):
        raise ValueError(f"{parameter_name} must use only letters, numbers, '.', '_', '+', ':', or '-'")
    return value


def _validate_command_tokens(command: str | Sequence[str] | None) -> list[str]:
    if command is None:
        return []
    if isinstance(command, str):
        tokens = [command]
    else:
        tokens = [cast(str, token) for token in command]

    for token in tokens:
        if not token:
            raise ValueError("Command tokens must be non-empty strings")
        if any(control in token for control in ("\x00", "\n", "\r", "\t")):
            raise ValueError("Command tokens must not contain control characters")
        if not _VALID_COMMAND_TOKEN_PATTERN.fullmatch(token):
            raise ValueError("Command tokens must use only safe shell-like characters")

    return tokens


@dataclass(init=False)
class DistroManager:  # pylint: disable=too-many-instance-attributes
    """Holds data related to supported distributions grouped by package manager."""

    names: list[str]
    package_manager: str
    install_repo_command: list[str] | str
    update_local_index_command: list[str] | str
    list_installed_packages_command: list[str] | str
    stable_package_name: str = "protonvpn-stable-release"
    beta_package_name: str = "protonvpn-beta-release"
    remove_old_package: bool = False

    def __init__(
        self,
        names: list[str] | str,
        package_manager: str,
        install_repo_command: list[str] | str,
        update_local_index_command: list[str] | str,
        reinstall_app_command: Optional[list[str] | str] = None,
        list_installed_packages_command: Optional[list[str] | str] = None,
        stable_package_name: str = "protonvpn-stable-release",
        beta_package_name: str = "protonvpn-beta-release",
        remove_old_package: bool = False,
        reinstall_app_commands: Optional[Sequence[Sequence[str] | str] | Sequence[str] | str] = None,
    ) -> None:
        self.names = [names] if isinstance(names, str) else list(names)
        self.package_manager = package_manager
        self.install_repo_command = install_repo_command
        self.update_local_index_command = update_local_index_command
        self.list_installed_packages_command = list_installed_packages_command or []
        self.stable_package_name = stable_package_name
        self.beta_package_name = beta_package_name
        self.remove_old_package = remove_old_package
        self.reinstall_app_command = reinstall_app_commands or reinstall_app_command

    @staticmethod
    def _normalize_command(command: str | Sequence[str] | None) -> list[str]:
        return _validate_command_tokens(command)

    @property
    def reinstall_app_commands(self) -> list[list[str]]:
        if self.reinstall_app_command is None:
            return []

        if isinstance(self.reinstall_app_command, str):
            return [[self.reinstall_app_command]]

        if not isinstance(self.reinstall_app_command, (list, tuple)):
            return []

        if not self.reinstall_app_command:
            return []

        first_item = self.reinstall_app_command[0]
        if isinstance(first_item, (list, tuple)):
            return [
                self._normalize_command(cast(str | Sequence[str], command)) for command in self.reinstall_app_command
            ]

        return [self._normalize_command(cast(str | Sequence[str], self.reinstall_app_command))]

    def _build_install_repo_command(self, package_to_remove: str, package_to_install: str) -> list[str]:
        """Builds argv command to install release package."""
        package_to_remove = _validate_package_name(package_to_remove, "package_to_remove")
        package_to_install = _validate_package_name(package_to_install, "package_to_install")
        install_repo_command = self._normalize_command(self.install_repo_command)
        if self.remove_old_package:
            return [*install_repo_command, package_to_remove, package_to_install]
        return [*install_repo_command, package_to_install]

    def build_update_command(self, package_to_remove: str, package_to_install: str) -> str:
        """Builds a shell-safe command string for installing release packages."""
        commands: list[str] = []
        install_repo_command = self._normalize_command(self.install_repo_command)
        update_local_index_command = self._normalize_command(self.update_local_index_command)
        if self.remove_old_package:
            commands.append(shlex.join([*install_repo_command, package_to_remove, package_to_install]))
        else:
            commands.append(shlex.join([*install_repo_command, package_to_install]))

        commands.append(shlex.join(update_local_index_command))
        commands.extend(shlex.join(command) for command in self.reinstall_app_commands)
        return " && ".join(command for command in commands if command)

    def build_update_commands(self, package_to_remove: str, package_to_install: str) -> list[list[str]]:
        """Builds argv commands to install release package and reinstall the app."""
        commands = [
            self._build_install_repo_command(package_to_remove, package_to_install),
            self._normalize_command(self.update_local_index_command),
            *self.reinstall_app_commands,
        ]
        return [c for c in commands if c]


DEBIAN_MANAGER = DistroManager(
    names=["debian", "ubuntu"],
    package_manager="/usr/bin/apt",
    install_repo_command=["/usr/bin/apt", "-y", "install"],
    list_installed_packages_command=["/usr/bin/apt", "list", "--installed"],
    update_local_index_command=["/usr/bin/apt", "update"],
    reinstall_app_commands=[
        ["/usr/bin/apt", "autoremove", "-y", "proton-vpn-gnome-desktop"],
        ["/usr/bin/apt", "install", "-y", "proton-vpn-gnome-desktop"],
    ],
    remove_old_package=False,  # debian handles old package removal when installing new one
)

FEDORA_MANAGER = DistroManager(
    names=["fedora"],
    package_manager="dnf",
    list_installed_packages_command=["rpm", "-qa"],
    install_repo_command=["dnf", "swap", "-y"],
    update_local_index_command=["dnf", "makecache"],
    reinstall_app_commands=[
        ["dnf", "remove", "-y", "proton-vpn-gnome-desktop"],
        ["dnf", "install", "-y", "proton-vpn-gnome-desktop"],
    ],
    remove_old_package=True,
)


class EarlyAccessDialog(Gtk.Dialog):
    """Dialog used to provide some visual feedback to the user on the status
    of early access toggle.

    It's worth noting that the dialog is not destroyed when closed but rather just hidden.
    It is destroyed only once the parent window is closed.
    """

    LOADING_VIEW = "loading"
    STATUS_VIEW = "status"
    TITLE = "Beta Access"

    def _on_close_clicked(self, _button: Gtk.Button):
        self.set_visible(False)

    def __init__(self) -> None:
        super().__init__()
        self.set_name("early-access-dialog")
        self.set_default_size(350, 200)
        self.set_modal(True)

        # We have to add a headerbar because we want to hide the close button,
        # which we don't have control otherwise.
        headerbar = Gtk.HeaderBar()
        title_label = Gtk.Label(label=self.TITLE)
        headerbar.set_title_widget(title_label)
        headerbar.set_decoration_layout("menu:")
        self.set_titlebar(headerbar)

        self._confirmation_button = Gtk.Button(label="_Close", use_underline=True)
        safe_signal_connect(self._confirmation_button, "clicked", self._on_close_clicked)
        self._spinner = Spinner(70)
        self._spinner.set_margin_top(20)
        self._active_view: Optional[str] = None
        self._suppress_present = bool(os.environ.get(_PYTEST_ENV_VAR))

        self._label = Gtk.Label()
        self._label.set_width_chars(50)
        self._label.set_max_width_chars(50)
        self._label.set_wrap(True)
        self._label.set_wrap_mode(Pango.WrapMode.WORD)
        self._label.set_property("xalign", 0)
        self._confirmation_button.add_css_class("primary")

        # pylint: disable=duplicate-code
        content_area = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_area.set_vexpand(True)
        content_area.set_margin_top(20)
        content_area.set_margin_bottom(20)
        content_area.set_margin_start(20)
        content_area.set_margin_end(20)
        content_area.set_spacing(20)  # pylint: disable=no-member
        content_area.append(self._label)
        content_area.append(self._spinner)
        content_area.append(self._confirmation_button)
        self.set_child(content_area)

    def display_loading_view(self, new_label_value: str):
        """Displays a loading view and blocking the close button."""
        self._confirmation_button.set_property("sensitive", False)
        self._spinner.set_property("visible", True)
        self._label.set_label(new_label_value)
        self._active_view = self.LOADING_VIEW
        if self._suppress_present:
            return
        self.present()

    def display_status_view(self, new_label_value: str):
        """Displays a status view, allowing to close the button."""
        self._confirmation_button.set_property("sensitive", True)
        self._spinner.set_property("visible", False)
        self._label.set_label(new_label_value)
        self._active_view = self.STATUS_VIEW
        if self._suppress_present:
            return
        self.present()


class EarlyAccessWidget(ToggleWidget):
    """Handles all early access operations.
    It takes care of checking if package manager exists, downloading,
    uninstall and installing packages.
    """

    SUPPORTED_DISTRO_MANAGERS = [FEDORA_MANAGER, DEBIAN_MANAGER]
    DISABLE_BETA_ACCESS_MESSAGE = "Disabling Beta access..."
    ENABLE_BETA_ACCESS_MESSAGE = "Enabling Beta access..."
    BETA_LABEL = "Beta access"
    BETA_DESCRIPTION = "Get early access and help us test new versions of Proton VPN."
    PKEXEC_COMMAND_TIMEOUT_SECONDS = 60

    def __init__(
        self,
        controller: Controller,
        distro_manager: Optional[DistroManager] = None,
        early_access_dialog: Optional[EarlyAccessDialog] = None,
    ):
        self._distro_manager = distro_manager
        # Cache the subprocess result so it is only fetched once per widget
        # instance.  Without this, _find_installed_repo_packages() would block
        # the GTK main thread twice: once during super().__init__() (via
        # _build_switch → get_setting) and once inside
        # can_early_access_be_displayed().
        self._packages_cache: Optional[Tuple[bool, bool]] = None

        # Pass enabled=False so ToggleWidget._build_switch() skips its
        # get_setting() call, which would otherwise block the main thread.
        # build_beta_upgrade() corrects the switch state after the cache is
        # warm (i.e. after can_early_access_be_displayed() returns True).
        super().__init__(
            controller=controller,
            title=self.BETA_LABEL,
            description=self.BETA_DESCRIPTION,
            setting_name="",
            requires_subscription_to_be_active=False,
            callback=self._on_switch_early_access_state,
            enabled=False,
        )
        self._controller = controller
        self._dialog = early_access_dialog or EarlyAccessDialog()
        self._suppress_toggle_callback = False
        self._operation_in_progress = False
        self._packages_lookup_future: Optional[Future[subprocess.CompletedProcess[bytes]]] = None
        safe_signal_connect(self._dialog, "response", lambda w, _: w.set_visible(False))
        self._start_packages_lookup()

    @property
    def distro_manager(self) -> Optional[DistroManager]:
        """Returns a distribution manager if the current one is none."""
        if self._distro_manager is None:
            self._distro_manager = self._get_system_distro_manager()

        return self._distro_manager

    def can_early_access_be_displayed(self) -> bool:
        """Determines if early access should be available."""
        # If we couldn't determine the package manager, don't show early access.
        if self.distro_manager is None:
            return False

        stable_package_installed, beta_package_installed = self._find_installed_repo_packages()

        # If we couldn't determine which release package is installed,
        # don't show early access.
        if not stable_package_installed and not beta_package_installed:
            return False

        # If we couldn't find `pkexec` binary on system, don't show early access.
        if not shutil.which("pkexec"):
            return False

        return True

    def set_initial_state(self) -> None:
        """Sets the switch initial state."""
        self.active = self.get_setting()

    def get_setting(self) -> bool:
        """Returns if early access is enabled, if the early access package
        was found on the system."""
        # If it's None then it means that we're running on either:
        # - Unsupported distribution
        # - Unsupported install method that does not allow to identify a package manager
        if self.distro_manager is None:
            return False

        _, beta_package_installed = self._find_installed_repo_packages()
        return beta_package_installed

    def _on_switch_early_access_state(self, _widget: object, new_value: bool, _data: object) -> None:
        if self._suppress_toggle_callback:
            return

        if self._operation_in_progress:
            return

        if new_value == self.get_setting():
            return

        logger.info(
            "Early access %s.",
            "enabled" if new_value else "disabled",
            category="ui",
            subcategory="early_access",
            event="toggle",
        )

        if new_value:
            self._enable_early_access()
        else:
            self._disable_early_access()

    def _disable_early_access(self) -> None:
        """Disables early access."""
        distro_manager = self.distro_manager
        if distro_manager is None:
            return
        self._operation_in_progress = True
        self._dialog.display_loading_view(self.DISABLE_BETA_ACCESS_MESSAGE)
        self._run_commands(
            distro_manager.beta_package_name, distro_manager.stable_package_name, early_access_enabled=False
        )

    def _enable_early_access(self) -> None:
        """Enables early access."""
        distro_manager = self.distro_manager
        if distro_manager is None:
            return
        self._operation_in_progress = True
        self._dialog.display_loading_view(self.ENABLE_BETA_ACCESS_MESSAGE)
        self._run_commands(
            distro_manager.stable_package_name, distro_manager.beta_package_name, early_access_enabled=True
        )

    @staticmethod
    def _summarize_command_output(result: subprocess.CompletedProcess[bytes]) -> str:
        stdout = _sanitize_output_text(result.stdout.decode("utf-8", errors="replace"))
        stderr = _sanitize_output_text(result.stderr.decode("utf-8", errors="replace"))

        summary_parts = []
        if stdout.strip():
            summary_parts.append(f"stdout={stdout.strip()}")
        if stderr.strip():
            summary_parts.append(f"stderr={stderr.strip()}")
        if not summary_parts:
            return "<empty>"
        return " | ".join(summary_parts)

    def _find_installed_repo_packages(self) -> Tuple[bool, bool]:
        """Returns if any of the repo packages are installed.

        If neither the beta and/or stable packages were found on the system, it points
        to the possibility that the app was installed via a 3rd party and via our official KBs.
        """
        if self._packages_cache is not None:
            return self._packages_cache

        if self._packages_lookup_future is not None and self._packages_lookup_future.done():
            try:
                result = self._packages_lookup_future.result()
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Timed out listing installed packages", category="subprocess", subcategory="command", event="run"
                )
                self._packages_cache = (False, False)
                return self._packages_cache
            except (OSError, subprocess.SubprocessError):
                logger.warning(
                    "Unable to list repo packages due to subprocess failure",
                    category="subprocess",
                    subcategory="command",
                    event="run",
                )
                self._packages_cache = (False, False)
                return self._packages_cache

            self._packages_cache = self._parse_installed_repo_packages_result(result)
            return self._packages_cache

        self._start_packages_lookup()
        return False, False

    def _start_packages_lookup(self) -> None:
        distro_manager = self.distro_manager
        if distro_manager is None:
            return

        if self._packages_lookup_future is not None and not self._packages_lookup_future.done():
            return

        self._packages_lookup_future = self._controller.run_subprocess(
            distro_manager.list_installed_packages_command,
            timeout=30,
        )
        self._packages_lookup_future.add_done_callback(self._on_packages_lookup_done)

    def _on_packages_lookup_done(self, future: Future) -> None:
        try:
            result = future.result()
        except subprocess.TimeoutExpired:
            logger.warning(
                "Timed out listing installed packages", category="subprocess", subcategory="command", event="run"
            )
            self._packages_cache = (False, False)
            return
        except (OSError, subprocess.SubprocessError):
            logger.warning(
                "Unable to list repo packages due to subprocess failure",
                category="subprocess",
                subcategory="command",
                event="run",
            )
            self._packages_cache = (False, False)
            return

        self._packages_cache = self._parse_installed_repo_packages_result(result)
        GLib.idle_add(self._refresh_switch_state_from_cache)

    def _refresh_switch_state_from_cache(self) -> bool:
        self._suppress_toggle_callback = True
        try:
            self.set_state(self.get_setting())
        finally:
            self._suppress_toggle_callback = False
        return False

    def _parse_installed_repo_packages_result(self, result: subprocess.CompletedProcess[bytes]) -> Tuple[bool, bool]:
        beta_repo_package_installed = False
        stable_repo_package_installed = False
        distro_manager = self.distro_manager

        if distro_manager is None:
            return stable_repo_package_installed, beta_repo_package_installed

        if self._command_failed(result):
            logger.warning(
                "Unable to list repo packages: %s",
                self._summarize_command_output(result),
                category="subprocess",
                subcategory="command",
                event="run",
            )
            return stable_repo_package_installed, beta_repo_package_installed

        for entry in result.stdout.decode("utf-8").split("\n"):
            if distro_manager.beta_package_name in entry:
                beta_repo_package_installed = True
                continue

            if distro_manager.stable_package_name in entry:
                stable_repo_package_installed = True
                continue

            if stable_repo_package_installed and beta_repo_package_installed:
                break

        return stable_repo_package_installed, beta_repo_package_installed

    def _run_commands(self, package_to_remove: str, package_to_install: str, early_access_enabled: bool) -> None:
        distro_manager = self.distro_manager
        if distro_manager is None:
            self._operation_in_progress = False
            self._dialog.display_status_view(
                f"It was not possible to {'enable' if early_access_enabled else 'disable'} Beta access.\n"
            )
            return

        def make_done_callback(command_index: int):
            def _done_callback(future: Future) -> None:
                on_handle_early_access(future, command_index)

            return _done_callback

        def queue_command(command_index: int) -> None:
            command = ["pkexec", *commands[command_index]]
            future = self._controller.run_subprocess(
                command,
                timeout=self.PKEXEC_COMMAND_TIMEOUT_SECONDS,
            )
            future.add_done_callback(make_done_callback(command_index))

        def on_handle_early_access(future: Future, index: int) -> None:
            try:
                result = future.result()
            except subprocess.TimeoutExpired:
                logger.warning(
                    "Timed out while fulfilling early access command",
                    category="subprocess",
                    subcategory="command",
                    event="run",
                )
                self._restore_switch_to_previous_state()
                self._operation_in_progress = False
                self._dialog.display_status_view(
                    f"It was not possible to {'enable' if early_access_enabled else 'disable'} Beta access.\n"
                )
                return
            except (OSError, subprocess.SubprocessError):
                logger.warning(
                    "Subprocess failure while fulfilling early access command",
                    category="subprocess",
                    subcategory="command",
                    event="run",
                )
                self._restore_switch_to_previous_state()
                self._operation_in_progress = False
                self._dialog.display_status_view(
                    f"It was not possible to {'enable' if early_access_enabled else 'disable'} Beta access.\n"
                )
                return

            if self._command_failed(result):
                logger.warning(
                    "Unable to fulfil command: %s",
                    self._summarize_command_output(result),
                    category="subprocess",
                    subcategory="command",
                    event="run",
                )
                self._restore_switch_to_previous_state()
                self._operation_in_progress = False
                self._dialog.display_status_view(
                    f"It was not possible to {'enable' if early_access_enabled else 'disable'} Beta access.\n"
                )
                return

            if index + 1 < len(commands):
                queue_command(index + 1)
                return

            logger.info(
                "Command successfully run: %s",
                self._summarize_command_output(result),
                category="subprocess",
                subcategory="command",
                event="run",
            )
            self._operation_in_progress = False
            self._dialog.display_status_view(
                f"Beta access has been {'enabled' if early_access_enabled else 'disabled'}.\n"
                "Please restart the app for changes to take effect."
            )

        allowed = {
            distro_manager.stable_package_name,
            distro_manager.beta_package_name,
        }
        if package_to_remove not in allowed or package_to_install not in allowed:
            logger.error(
                "Unexpected package names passed to pkexec: remove=%r install=%r",
                package_to_remove,
                package_to_install,
                category="subprocess",
                subcategory="command",
                event="run",
            )
            raise ValueError(f"Unexpected package names: {package_to_remove!r}, {package_to_install!r}")
        distro_manager.build_update_command(package_to_remove, package_to_install)
        commands = distro_manager.build_update_commands(package_to_remove, package_to_install)
        if not isinstance(commands, (list, tuple)):
            logger.warning(
                "Unable to build early access update commands for distro manager %r",
                distro_manager,
                category="subprocess",
                subcategory="command",
                event="run",
            )
            self._operation_in_progress = False
            self._dialog.display_status_view(
                f"It was not possible to {'enable' if early_access_enabled else 'disable'} Beta access.\n"
            )
            return

        if not commands:
            self._operation_in_progress = False
            self._dialog.display_status_view(
                f"It was not possible to {'enable' if early_access_enabled else 'disable'} Beta access.\n"
            )
            return

        queue_command(0)

    def _get_system_distro_manager(self) -> Optional[DistroManager]:
        for supported_distro_manager in self.SUPPORTED_DISTRO_MANAGERS:
            if shutil.which(supported_distro_manager.package_manager):
                for supported_distro in supported_distro_manager.names:
                    if any(dist == supported_distro for dist in COMPATIBLE_DISTRIBUTIONS):
                        return supported_distro_manager

        return None

    def _restore_switch_to_previous_state(self):
        def _restore() -> bool:
            self._suppress_toggle_callback = True
            try:
                self.set_state(self.get_setting())
            finally:
                self._suppress_toggle_callback = False
            return False

        GLib.idle_add(_restore)

    def _command_failed(self, result: subprocess.CompletedProcess[bytes]) -> bool:
        return result.returncode != 0

"""
Issue report module.


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

from __future__ import annotations

import io
import os
import re
import shutil
import subprocess  # nosec B404 # nosemgrep: gitlab.bandit.B404
from concurrent.futures import Future
from tempfile import NamedTemporaryFile, _TemporaryFileWrapper
from typing import TYPE_CHECKING, Any, List, Optional, cast

from gi.repository import GLib, Gtk

from proton.vpn import logging
from proton.vpn.app.gtk import __version__
from proton.vpn.app.gtk.utils.safe_signal_connect import safe_signal_connect
from proton.vpn.app.gtk.widgets.main.notification_bar import NotificationBar

if TYPE_CHECKING:
    from proton.vpn.app.gtk.app import MainWindow
    from proton.vpn.app.gtk.controller import Controller
    from proton.vpn.app.gtk.utils.executor import AsyncExecutor


logger = logging.getLogger(__name__)

_ALLOWED_JOURNALCTL_EXECUTABLE = "/usr/bin/journalctl"

_SENSITIVE_PATTERN = re.compile(
    r"(?i)("
    r"authorization\s*:\s*bearer\s+[^\s,;]+"
    r"|token\s*[:=]\s*[^\s,;]+"
    r"|password\s*[:=]\s*[^\s,;]+"
    r"|secret\s*[:=]\s*[^\s,;]+"
    r"|api[_-]?key\s*[:=]\s*[^\s,;]+"
    r")"
)


def _sanitize_report_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    if _SENSITIVE_PATTERN.search(value):
        return "[REDACTED]"
    return value


class BugReportDialog(Gtk.Dialog):  # pylint: disable=too-many-instance-attributes
    """Widget used to report bug/issues for this community build."""

    WIDTH = 400
    HEIGHT = 240
    BUG_REPORT_RECIPIENT = "libertypo@proton.me"
    BUG_REPORT_MESSAGE = (
        "Please report issues by email. Include app version, steps to reproduce, and logs if available."
    )

    def __init__(
        self,
        controller: Controller,
        main_window: MainWindow,
        notification_bar: NotificationBar | None = None,
        log_collector: Optional["LogCollector"] = None,
    ):
        super().__init__()
        self.set_name("bug-report-dialog")
        self._controller = controller
        self._main_window = main_window
        self.notification_bar = notification_bar or NotificationBar()
        self._log_collector = log_collector or LogCollector(self._controller.executor)

        self.set_title("Report an Issue (Community Build)")
        self.set_default_size(BugReportDialog.WIDTH, BugReportDialog.HEIGHT)

        self.cancel_button = Gtk.Button(label="_Close", use_underline=True)

        self.cancel_button.add_css_class("danger")

        safe_signal_connect(self.cancel_button, "clicked", self._on_cancel_clicked)

        self._generate_fields()

    def _on_cancel_clicked(self, _button: Gtk.Button):
        self.close()

    def _generate_fields(self):
        """Generates the message and contact details for issue reporting."""
        layout = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        layout.set_margin_top(0)
        layout.set_margin_bottom(0)
        layout.set_margin_start(0)
        layout.set_margin_end(0)
        layout.append(self.notification_bar)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        content.set_name("bug-report-content")
        layout.append(content)

        self.report_message_label = Gtk.Label(label=self.BUG_REPORT_MESSAGE)
        self.report_message_label.set_halign(Gtk.Align.START)
        self.report_message_label.set_wrap(True)
        self.report_message_label.set_xalign(0)
        self.report_message_label.set_selectable(False)
        self.report_message_label.set_margin_bottom(8)
        content.append(self.report_message_label)

        self.report_email_label = Gtk.Label(label=f"Report issues at: {self.BUG_REPORT_RECIPIENT}")
        self.report_email_label.set_halign(Gtk.Align.START)
        self.report_email_label.set_wrap(True)
        self.report_email_label.set_xalign(0)
        self.report_email_label.set_selectable(True)
        content.append(self.report_email_label)

        actions = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        actions.set_halign(Gtk.Align.END)
        actions.append(self.cancel_button)
        content.append(actions)

        layout.set_margin_top(0)
        layout.set_margin_bottom(0)
        layout.set_margin_start(0)
        layout.set_margin_end(0)
        layout.set_spacing(20)
        self.set_child(layout)


class LogCollector:  # pylint: disable=too-few-public-methods
    """Collects all necessary logs needed for issue reporting."""

    JOURNALCTL_TIMEOUT_SECONDS = 15

    def __init__(self, executor: AsyncExecutor):
        self._executor = executor

    def get_logs(self) -> Future[List[io.IOBase]]:
        """Generates and returns all available logs asynchronously."""

        def _collect_logs():
            return [
                self._get_app_log(),
                self._generate_network_manager_log(),
                self._get_daemon_log(),
            ]

        executor = cast(Any, self._executor)
        return cast(Future[List[io.IOBase]], executor.submit(_collect_logs))

    def _get_app_log(self) -> io.IOBase:
        root_logger = logger.logger.root
        for handler in root_logger.handlers:
            if handler.__class__.__name__ == "RotatingFileHandler":
                with (
                    open(handler.baseFilename, "rb") as source_log,
                    NamedTemporaryFile(
                        prefix="AppLog",
                        suffix=".log",
                        delete=False,
                    ) as temp_file,
                ):
                    for line in source_log:
                        sanitized_line = _sanitize_report_text(line.decode("utf-8", errors="replace"))
                        temp_file.write(sanitized_line.encode("utf-8"))

                    return open(temp_file.name, "rb")

        raise RuntimeError("App logs not found.")

    def _run_journalctl_into_temp_file(self, args: List[str], prefix: str) -> io.IOBase:
        temp_path: Optional[str] = None
        temp_file: Optional[_TemporaryFileWrapper[bytes]] = None
        try:
            if not args or not isinstance(args, list):
                raise RuntimeError("Journalctl command is invalid")
            if not self._is_allowed_journalctl_executable(args[0]):
                raise RuntimeError("Journalctl uses an unsupported executable")
            if any(not isinstance(token, str) or not token for token in args):
                raise RuntimeError("Journalctl command contains invalid tokens")
            if any("\x00" in token for token in args):
                raise RuntimeError("Journalctl command contains invalid control characters")

            temp_file = NamedTemporaryFile(prefix=prefix, suffix=".log", delete=False)
            temp_path = temp_file.name
            os.chmod(temp_path, 0o600)
            try:
                process = subprocess.run(  # nosec B603
                    args,
                    stdout=temp_file,
                    check=False,
                    timeout=self.JOURNALCTL_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"{prefix} logs timed out during generation.") from exc

            if process.returncode == 0:
                sanitized_temp_path = f"{temp_path}.sanitized"
                with open(temp_path, "rb") as source_log, open(sanitized_temp_path, "wb") as sanitized_log:
                    for raw_line in source_log:
                        sanitized_line = _sanitize_report_text(raw_line.decode("utf-8", errors="replace"))
                        sanitized_log.write(sanitized_line.encode("utf-8"))

                os.replace(sanitized_temp_path, temp_path)
                os.chmod(temp_path, 0o600)

                temp_file.close()
                temp_file = None
                return open(temp_path, "rb")

            raise RuntimeError(f"{prefix} logs could not be generated.")
        finally:
            if temp_file is not None:
                temp_file.close()
            if temp_path is not None:
                for candidate in (temp_path, f"{temp_path}.sanitized"):
                    if candidate and os.path.exists(candidate):
                        try:
                            os.unlink(candidate)
                        except OSError:
                            logger.warning(
                                "Unable to remove temporary log file: %s",
                                candidate,
                            )

    @staticmethod
    def _is_allowed_journalctl_executable(executable: str) -> bool:
        if executable in {"journalctl", _ALLOWED_JOURNALCTL_EXECUTABLE}:
            return True

        resolved_executable = shutil.which(executable)
        if not resolved_executable:
            return False

        return os.path.realpath(resolved_executable) == os.path.realpath(_ALLOWED_JOURNALCTL_EXECUTABLE)

    def _get_daemon_log(self) -> io.IOBase:
        args = [
            "journalctl",
            "-u",
            "me.proton.vpn.split_tunneling",
            "--no-pager",
            "--utc",
            "--since=-1d",
            "--no-hostname",
        ]
        return self._run_journalctl_into_temp_file(args, "SplitTunneling")

    def _generate_network_manager_log(self) -> io.IOBase:
        args = [
            "journalctl",
            "-u",
            "NetworkManager",
            "--no-pager",
            "--utc",
            "--since=-1d",
            "--no-hostname",
        ]
        return self._run_journalctl_into_temp_file(args, "NetworkManager")

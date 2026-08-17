"""
Generic exception handler.


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

import re
import sys
import threading
from typing import TYPE_CHECKING, Optional

import gi
from proton.session.exceptions import (
    ProtonAPIAuthenticationNeeded,
    ProtonAPIError,
    ProtonAPIMissingScopeError,
    ProtonAPINotReachable,
)
from proton.vpn.connection.exceptions import AuthenticationError, HardJailedTwoFAError, NotYetValidCertificateError
from proton.vpn.session.exceptions import ServerNotFoundError

from proton.vpn import logging
from proton.vpn.app.gtk.widgets.main.notifications import DialogButton

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402,E501 # pylint: disable=wrong-import-position,wrong-import-order

NO_SPACE_LEFT_ON_DEVICE_ERRNO = 28

if TYPE_CHECKING:
    from proton.vpn.app.gtk.controller import Controller
    from proton.vpn.app.gtk.widgets.main.main_widget import MainWidget

logger = logging.getLogger(__name__)

_SENSITIVE_PATTERN = re.compile(
    r"(?i)("
    r"authorization\s*:\s*bearer\s+[^\s,;]+"
    r"|token\s*[:=]\s*[^\s,;]+"
    r"|password\s*[:=]\s*[^\s,;]+"
    r"|secret\s*[:=]\s*[^\s,;]+"
    r"|api[_-]?key\s*[:=]\s*[^\s,;]+"
    r")"
)


def _sanitize_text(value: object) -> str:
    if not isinstance(value, str):
        return ""

    return _SENSITIVE_PATTERN.sub("[REDACTED]", value)


def _safe_exception_message(exc_type: type[BaseException], exc_value: BaseException | None) -> str:
    if exc_value is None:
        return exc_type.__name__

    return _sanitize_text(str(exc_value))


class ExceptionHandler:
    """Handles generic exceptions before they bubble all the way up."""

    GENERIC_ERROR_TITLE = "Something went wrong"
    GENERIC_ERROR_MESSAGE = "We're sorry, an unexpected error occurred.Please try again."
    SERVER_NOT_FOUND_TITLE = "Unable to find server"
    PROTON_API_NOT_REACHABLE_MESSAGE = "Our servers are not reachable. Please check your internet connection."
    VPN_AUTHENTICATION_ERROR_TITLE = "VPN connection error"
    VPN_AUTHENTICATION_ERROR_MESSAGE = (
        "Proton VPN could not connect to the VPN and blocked access to Internet to protect your IP."
        '\n\nClick "Cancel Connection" to restore your Internet connection. '
        "If the issue persists please try to sign out and in."
    )
    VPN_HARD_JAILED_2FA_ERROR_TITLE = "2FA Required"
    VPN_HARD_JAILED_2FA_ERROR_MESSAGE = (
        "You are connected to the VPN, but all traffic is blocked.\nYou need to"
        " go to the authentication page provided by security and authenticate"
        " with your hardware key.\nAfter that, the traffic will be enabled."
    )
    TIME_OUT_OF_SYNC_ERROR_TITLE = "Update system clock"
    TIME_OUT_OF_SYNC_ERROR_MESSAGE = (
        "Looks like your system clock is out of sync.\n"
        "This may cause issues when connecting to VPN.\n"
        "Update your system time and try to connect again."
    )

    def __init__(self, main_widget: Optional["MainWidget"] = None, controller: Optional["Controller"] = None):
        super().__init__()
        self.main_widget = main_widget
        self.controller = controller
        self._previous_sys_excepthook = sys.excepthook
        self._previous_threading_excepthook = threading.excepthook

    def enable(self):
        """
        Enables the exception handler. Note that the exception handler
        should be enabled only after the main application window has been
        presented to the user so that error dialogs can actually be shown.
        """
        self._previous_sys_excepthook = sys.excepthook
        self._previous_threading_excepthook = threading.excepthook

        # Handle exceptions bubbling up in the main thread.
        sys.excepthook = self.handle_exception

        # Handle exceptions bubbling up in threads started with Thread.run().
        # Notice that an exception raised from a thread managed by a
        # ThreadPoolExecutor won't bubble up, as the executor won't allow it.
        # In this case, make sure that you call Future.result() on the future
        # returned by ThreadPoolExecutor.submit() in the main thread
        # (e.g. using GLib.idle_add()).
        threading.excepthook = self.handle_thread_exception

    def disable(self):
        """Disables the exception handler. The exception handler should
        be disabled as soon as the main application window is closed. This
        is specially important in tests, as the python process might run
        other test code."""
        sys.excepthook = self._previous_sys_excepthook
        threading.excepthook = self._previous_threading_excepthook

    def handle_thread_exception(self, args):
        """
        When the application exception handler is enabled, this method
        is triggered on errors that happened on threads.
        :param args: dictionary passed to threading.excepthook. For more info:
        https://docs.python.org/3/library/threading.html#threading.excepthook
        """
        GLib.idle_add(self.handle_exception, args.exc_type, args.exc_value, args.exc_traceback)

    def handle_exception(self, exc_type, exc_value, exc_traceback):
        """
        When the application exception handler is enabled, this method is
        triggered on errors that were not explicitly handled by the main
        application thread.
        :param exc_type: Type of the exception.
        :param exc_value: Instance of the exception. It might be None if the
        exception was triggered using an Exception class, rather than an object
        (e.g. raise Exception, instead of raise Exception()).
        :param exc_traceback: The exception traceback.
        """
        if self._dispatch_known_exception(exc_type, exc_value, exc_traceback):
            return

        if issubclass(exc_type, AssertionError):
            # We shouldn't catch assertion errors raised by tests.
            raise exc_value

        if issubclass(exc_type, Exception):
            self._on_exception(exc_type, exc_value, exc_traceback)
            return

        raise exc_value if exc_value else exc_type

    def _dispatch_known_exception(self, exc_type, exc_value, exc_traceback) -> bool:
        handlers = (
            (
                issubclass(exc_type, ProtonAPIAuthenticationNeeded),
                lambda: self._on_proton_api_authentication_needed(),
            ),
            (
                issubclass(exc_type, ProtonAPINotReachable),
                lambda: self._on_proton_api_not_reachable(exc_type, exc_value, exc_traceback),
            ),
            (
                isinstance(exc_value, ProtonAPIMissingScopeError),
                lambda: self._on_proton_api_missing_scope_error(exc_value),
            ),
            (
                isinstance(exc_value, ProtonAPIError) and bool(exc_value.error),
                lambda: self._on_proton_api_error(exc_type, exc_value, exc_traceback),
            ),
            (
                isinstance(exc_value, ServerNotFoundError),
                lambda: self._on_server_not_found(exc_type, exc_value, exc_traceback),
            ),
            (
                isinstance(exc_value, AuthenticationError),
                lambda: self._on_vpn_authentication_error(exc_type, exc_value, exc_traceback),
            ),
            (
                isinstance(exc_value, HardJailedTwoFAError),
                lambda: self._on_hard_jailed_2fa_error(exc_type, exc_value, exc_traceback),
            ),
            (
                isinstance(exc_value, NotYetValidCertificateError),
                lambda: self._on_certificate_not_yet_valid_error(
                    exc_type,
                    exc_value,
                    exc_traceback,
                ),
            ),
            (
                isinstance(exc_value, OSError) and exc_value.errno == NO_SPACE_LEFT_ON_DEVICE_ERRNO,
                lambda: self._on_no_space_left_on_device(exc_type, exc_value, exc_traceback),
            ),
        )

        for condition, handler in handlers:
            if condition:
                handler()
                return True

        return False

    def _on_proton_api_authentication_needed(self):
        logger.warning("Authentication required.", category="API", event="WARNING")
        if self.main_widget:
            self.main_widget.on_session_expired()

    def _on_proton_api_not_reachable(self, exc_type, exc_value, exc_traceback):
        if self.main_widget:
            self.main_widget.notifications.show_error_message(
                self.PROTON_API_NOT_REACHABLE_MESSAGE,
            )
        logger.warning(
            "API not reachable: %s", _safe_exception_message(exc_type, exc_value), category="API", event="ERROR"
        )

    def _on_proton_api_missing_scope_error(self, missing_scope_error: ProtonAPIMissingScopeError):
        """The user is valid but lacks VPN permissions."""
        logger.warning(_sanitize_text(missing_scope_error.error), category="APP", event="WARNING")

        if self.main_widget:
            self._logout_and_show_missing_scope_dialog(missing_scope_error)

    def _logout_and_show_missing_scope_dialog(self, error: ProtonAPIMissingScopeError):
        """This method is called by the exception handler when the user
        lacks VPN permissions."""
        main_widget = self.main_widget
        if main_widget is None:
            raise RuntimeError("Main widget is required to show missing scope dialog")
        main_widget.logout()

        def on_dialog_closed(response_type: int):
            if Gtk.ResponseType.OK == response_type:
                Gio.AppInfo.launch_default_for_uri("https://protonvpn.com/support/assign-vpn-connection", None)

        error_details = error.json_data["Details"]
        actions = error_details.get("Actions", [])

        buttons = []
        for action in actions:
            if action["Code"] == "AssignConnections":
                buttons.append(DialogButton(label=action["Name"], response_type=Gtk.ResponseType.OK))
        buttons.append(DialogButton(label="Sign in again", response_type=Gtk.ResponseType.CLOSE))

        main_widget.notifications.show_error_dialog(
            title=error_details["Title"],
            message=error_details["Body"],
            hint=error_details.get("Hint"),
            message_type=Gtk.MessageType.OTHER,
            buttons=buttons,
            on_dialog_closed=on_dialog_closed,
        )

    def _on_proton_api_error(self, exc_type, exc_value, exc_traceback):
        if self.main_widget:
            self.main_widget.notifications.show_error_message(exc_value.error)
        logger.error(_sanitize_text(exc_value.error), category="APP", event="ERROR")

    def _on_server_not_found(self, exc_type, exc_value, exc_traceback):
        if self.main_widget:
            self.main_widget.notifications.show_error_dialog(title=self.SERVER_NOT_FOUND_TITLE, message=str(exc_value))
        logger.error(_safe_exception_message(exc_type, exc_value), category="APP", event="ERROR")

    def _on_vpn_authentication_error(self, exc_type, exc_value, exc_traceback):
        if self.main_widget:
            self.main_widget.notifications.show_error_dialog(
                title=self.VPN_AUTHENTICATION_ERROR_TITLE, message=self.VPN_AUTHENTICATION_ERROR_MESSAGE
            )

        logger.error(_safe_exception_message(exc_type, exc_value), category="APP", event="ERROR")

    def _on_hard_jailed_2fa_error(self, exc_type, exc_value, exc_traceback):
        if self.main_widget:
            self.main_widget.notifications.show_error_dialog(
                title=self.VPN_HARD_JAILED_2FA_ERROR_TITLE, message=self.VPN_HARD_JAILED_2FA_ERROR_MESSAGE
            )

        logger.error(_safe_exception_message(exc_type, exc_value), category="APP", event="ERROR")

    def _on_certificate_not_yet_valid_error(self, exc_type, exc_value, exc_traceback):
        if self.main_widget:
            self.main_widget.notifications.show_error_dialog(
                title=self.TIME_OUT_OF_SYNC_ERROR_TITLE, message=self.TIME_OUT_OF_SYNC_ERROR_MESSAGE
            )

        logger.error(_safe_exception_message(exc_type, exc_value), category="APP", event="ERROR")

    def _on_no_space_left_on_device(self, exc_type, exc_value, exc_traceback):
        logger.error(_safe_exception_message(exc_type, exc_value), category="APP", event="ERROR")
        if self.main_widget:
            self.main_widget.notifications.show_error_dialog(
                title="No space left on device", message="There is not enough space left on your device."
            )

    def _on_exception(self, exc_type, exc_value, exc_traceback):
        if self.main_widget:
            self.main_widget.notifications.show_error_dialog(
                title=self.GENERIC_ERROR_TITLE,
                message=self.GENERIC_ERROR_MESSAGE,
            )
        logger.critical(
            "Unexpected error: %s", _safe_exception_message(exc_type, exc_value), category="APP", event="CRASH"
        )

        if self.controller:
            sanitized_value = exc_value
            if exc_value is not None:
                sanitized_message = _sanitize_text(str(exc_value))
                try:
                    sanitized_value = exc_value.__class__(sanitized_message)
                except Exception:  # pragma: no cover - defensive fallback
                    sanitized_value = Exception(sanitized_message)
            self.controller.send_error_to_proton((exc_type, sanitized_value, exc_traceback))

    def __enter__(self):
        self.enable()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.disable()

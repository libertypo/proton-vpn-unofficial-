"""
Network connectivity monitoring.


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
import shutil
import subprocess  # nosec B404 # nosemgrep: gitlab.bandit.B404
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable, Optional

from gi.repository import GLib

from proton.vpn import logging  # noqa: E402 # pylint: disable=wrong-import-position
from proton.vpn.app.gtk.utils.glib import run_once, run_periodically

logger = logging.getLogger(__name__)
NETWORK_CHECK_TIMEOUT_SECONDS = 2.0


def check_for_network_connectivity() -> bool:
    """
    Checks for network connectivity and returns True if connected or False otherwise.
    """
    # 192.0.2.1 is used because is a valid IP that won't be in use,
    # since it is reserved for documentation purposes:
    # https://www.rfc-editor.org/rfc/rfc5737.html
    ip_command = shutil.which("ip")
    if ip_command is None:
        logger.warning("Unable to check network connectivity: 'ip' command not found")
        return False

    resolved_ip_command = os.path.realpath(ip_command)
    if not resolved_ip_command or not resolved_ip_command.endswith("/ip"):
        logger.warning("Network connectivity check uses an unexpected ip executable")
        return False

    try:
        result = subprocess.run(  # nosec B603
            [resolved_ip_command, "route", "get", "192.0.2.1"],
            check=False,
            capture_output=True,
            timeout=NETWORK_CHECK_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Network connectivity check timed out")
        return False

    return result.returncode == 0


class NetworkMonitor:
    """
    After being enabled, it calls the callback set on the network_up_callback
    attribute whenever connectivity to the Internet is detected.

    Note that it requires a GLib main loop to be running, as the current
    implementation relies on it to poll for network state changes.

    Usage example:
    .. code-block:: python
        monitor = NetworkMonitor()
        monitor.network_up_callback = on_network_up
        monitor.enable()
        GLib.MainLoop().run()  # Only required if there is not already a main loop.

    Attributes:
        network_up_callback: callable that will be called whenever connectivity
        to the Internet is detected.
    """

    def __init__(self, pool: ThreadPoolExecutor, polling_interval_ms: int = 5000):
        self._pool = pool
        self._polling_interval_ms = polling_interval_ms
        self._is_network_up: Optional[bool] = None
        self._polling_handler_id: Optional[int] = None
        self._active_poll_future: Optional[Future] = None
        self.network_up_callback: Optional[Callable] = None

    def enable(self) -> None:
        """
        Enables the network connectivity monitor.

        It runs the `check_network_state_async` method periodically on the GLib main loop.
        """
        self._polling_handler_id = run_periodically(
            interval_ms=self._polling_interval_ms, function=self.check_network_state_async
        )

    def disable(self) -> None:
        """Disables the network connectivity monitor."""
        if self._polling_handler_id is not None:
            GLib.source_remove(self._polling_handler_id)
            self._polling_handler_id = None
        self._active_poll_future = None
        self._is_network_up = None
        self.network_up_callback = None

    def check_network_state_async(self) -> Future:
        """Checks what's the network state."""
        if self._active_poll_future and not self._active_poll_future.done():
            return self._active_poll_future

        future = self._pool.submit(self._poll_network_state)
        self._active_poll_future = future

        def _clear_active_poll_future(_future: Future):
            # Only clear the active future if it still points to this completed task.
            if self._active_poll_future is _future:
                self._active_poll_future = None

        future.add_done_callback(_clear_active_poll_future)
        return future

    def _poll_network_state(self) -> None:
        network_up = check_for_network_connectivity()
        network_just_went_up = (
            not self.is_network_up  # noqa: E501 # pylint: disable=line-too-long # nosemgrep: python.lang.maintainability.is-function-without-parentheses.is-function-without-parentheses
            and network_up
        )
        self._is_network_up = network_up

        if network_just_went_up and self.network_up_callback:
            run_once(self.network_up_callback)

    @property
    def is_network_up(self) -> bool:
        """
        Returns True if the device is connected to the network or False otherwise.
        Note: the value returned is based on the last check_network_state_async call.
        """
        return bool(self._is_network_up)

    @property
    def is_enabled(self) -> bool:
        """Returns whether the network monitor is enabled or not."""
        return self._polling_handler_id is not None

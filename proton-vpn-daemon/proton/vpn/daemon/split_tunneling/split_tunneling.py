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
import asyncio
from typing import Optional

from proton.vpn import logging
from proton.vpn.core.settings import SplitTunnelingConfig

from proton.vpn.daemon.split_tunneling.apps.service import AppBasedSplitTunnelingService

logger = logging.getLogger(__name__)


class SplitTunnelingService:
    """Implements the Split Tunneling feature."""

    def __init__(
            self,
            config_by_uid: Optional[dict[int, SplitTunnelingConfig]] = None,
            app_service: Optional[AppBasedSplitTunnelingService] = None
    ):
        self._config_by_uid = config_by_uid or {}
        self._app_split_tunneling = app_service or AppBasedSplitTunnelingService()
        self._lock = asyncio.Lock()

    async def set_config(self, uid: int, config: SplitTunnelingConfig):
        """
        Applies split tunneling configuration.
        @param uid: the unix user ID to apply the configuration for.
        @param config: the split tunneling configuration.
        """
        async with self._lock:
            logger.info("Setting %s for user %s", config, uid)
            self._config_by_uid[uid] = config

            if self._is_app_based_config():
                self._app_split_tunneling.start(self._config_by_uid)
            else:
                await self._app_split_tunneling.stop()

    async def clear_config(self, uid: int):
        """
        Clears split tunneling configuration for the specified user id:
        @param uid: the unix user ID to clear the configuration for.
        """
        async with self._lock:
            logger.info("Clearing config for user %s", uid)
            if uid in self._config_by_uid:
                del self._config_by_uid[uid]

                if self._is_app_based_config():
                    self._app_split_tunneling.start(self._config_by_uid)
                else:
                    await self._app_split_tunneling.stop()

    def get_config(self, uid: int) -> Optional[SplitTunnelingConfig]:
        """
        Returns the split tunneling configuration for a given user id.
        @param uid: the unix user id.
        @returns: the split tunneling configuration, if existing. Otherwise `None`.
        """
        return self._config_by_uid.get(uid)

    def get_all_configs(self) -> list[tuple[int, SplitTunnelingConfig]]:
        """
        Returns all available split tunneling configurations.
        @returns: a list of tuples where each tuple contains the user id
            and the split tunneling configuration, in this order.
        """
        return list(self._config_by_uid.items())

    def log_status(self):
        """Log ST service status"""
        logger.info("============Split Tunneling service status===========")
        self._app_split_tunneling.log_status()
        logger.info("=====================================================")

    def launch_app(self, uid: int, app_id: str):
        """Launch a selected app and return its managed status."""
        self._app_split_tunneling.launch_selected(uid, app_id)
        return self._app_split_tunneling.get_launch_status(uid, app_id)

    def adopt_app(self, uid: int, app_id: str, unit: str):
        """Adopt an explicitly named existing scope and return its status."""
        self._app_split_tunneling.adopt_selected(uid, app_id, unit)
        return self._app_split_tunneling.get_launch_status(uid, app_id)

    def get_app_status(self, uid: int, app_id: str):
        """Return launch/adoption status for one app."""
        return self._app_split_tunneling.get_launch_status(uid, app_id)

    def _is_app_based_config(self) -> bool:
        return any(
            any(path for path in config.app_paths) or any(
                getattr(config, "app_ids", [])
            )  # any non-empty application identity
            for config in self._config_by_uid.values()    # on any user config
        )

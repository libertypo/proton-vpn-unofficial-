"""
App configuration module.


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

import os
from dataclasses import asdict, dataclass
from typing import Any, Optional, TypedDict

from proton.utils.environment import VPNExecutionEnvironment

THEME_PREFERENCE_SYSTEM = "system"
THEME_PREFERENCE_DARK = "dark"
THEME_PREFERENCE_LIGHT = "light"
ALLOWED_THEME_PREFERENCES = {
    THEME_PREFERENCE_SYSTEM,
    THEME_PREFERENCE_DARK,
    THEME_PREFERENCE_LIGHT,
}


class AppConfigDict(TypedDict):
    tray_pinned_servers: list[Any]
    connect_at_app_startup: Optional[str]
    start_app_minimized: bool
    theme_preference: str


DEFAULT_APP_CONFIG: AppConfigDict = {
    "tray_pinned_servers": [],
    "connect_at_app_startup": None,
    "start_app_minimized": False,
    "theme_preference": THEME_PREFERENCE_SYSTEM,
}

APP_CONFIG = os.path.join(VPNExecutionEnvironment().path_config, "app-config.json")


@dataclass
class AppConfig:
    """Contains configurations that are app specific."""

    tray_pinned_servers: list[Any]
    connect_at_app_startup: Optional[str]
    start_app_minimized: bool
    theme_preference: str

    @staticmethod
    def from_dict(data: dict[str, object]) -> AppConfig:
        """Creates and returns `AppConfig` from the provided dict."""
        tray_pinned_servers_raw = data.get("tray_pinned_servers", [])
        tray_pinned_servers = tray_pinned_servers_raw if isinstance(tray_pinned_servers_raw, list) else []

        connect_at_app_startup_raw = data.get("connect_at_app_startup")
        connect_at_app_startup = (
            connect_at_app_startup_raw.upper()
            if isinstance(connect_at_app_startup_raw, str) and connect_at_app_startup_raw
            else None
        )

        start_app_minimized_raw = data.get("start_app_minimized", False)
        start_app_minimized = start_app_minimized_raw if isinstance(start_app_minimized_raw, bool) else False

        theme_preference_raw = data.get("theme_preference", THEME_PREFERENCE_SYSTEM)
        theme_preference = str(theme_preference_raw).lower()
        if theme_preference not in ALLOWED_THEME_PREFERENCES:
            theme_preference = THEME_PREFERENCE_SYSTEM

        return AppConfig(
            tray_pinned_servers=tray_pinned_servers,
            connect_at_app_startup=connect_at_app_startup,
            start_app_minimized=start_app_minimized,
            theme_preference=theme_preference,
        )

    def to_dict(self) -> dict:
        """Converts the class to dict."""
        return asdict(self)

    @staticmethod
    def default() -> AppConfig:
        """Creates and returns `AppConfig` from default app configurations."""
        return AppConfig(
            tray_pinned_servers=DEFAULT_APP_CONFIG["tray_pinned_servers"],
            connect_at_app_startup=DEFAULT_APP_CONFIG["connect_at_app_startup"],
            start_app_minimized=DEFAULT_APP_CONFIG["start_app_minimized"],
            theme_preference=DEFAULT_APP_CONFIG["theme_preference"],
        )

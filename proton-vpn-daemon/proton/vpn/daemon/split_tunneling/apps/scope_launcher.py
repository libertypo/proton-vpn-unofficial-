"""Launch applications inside user systemd scopes."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Mapping, Sequence

from proton.vpn.daemon.split_tunneling.apps.desktop_entry import DesktopEntryResolver


@dataclass(frozen=True)
class ScopeLaunch:
    """Description of an application launched in a transient user scope."""

    uid: int
    unit: str
    command: tuple[str, ...]
    pid: int


class ScopeLauncher:
    """Create transient user scopes without embedding app-specific logic."""

    def __init__(
        self,
        runner=subprocess.Popen,
        environ: Mapping[str, str] | None = None,
        desktop_entries: DesktopEntryResolver | None = None,
    ):
        self._runner = runner
        self._environ = dict(environ or os.environ)
        self._desktop_entries = desktop_entries or DesktopEntryResolver()

    def command(
        self,
        uid: int,
        unit: str,
        executable: str,
        arguments: Sequence[str] = (),
    ) -> list[str]:
        """Build a systemd-run command for a transient user scope."""
        if uid < 0:
            raise ValueError(f"Invalid user ID: {uid}")
        if not unit.endswith(".scope") or "/" in unit or ".." in unit:
            raise ValueError(f"Invalid scope unit: {unit!r}")
        if not executable or executable.startswith("-"):
            raise ValueError("Invalid executable")

        return [
            "systemd-run",
            "--user",
            "--machine", f"{uid}@.host",
            "--scope",
            "--uid", str(uid),
            "--unit", unit,
            "--quiet",
            executable,
            *arguments,
        ]

    @staticmethod
    def unit_for_app(uid: int, app_id: str) -> str:
        """Build a stable dedicated scope unit for one user's selected app."""
        if uid < 0 or not app_id or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", app_id):
            raise ValueError(f"Invalid application identity: uid={uid}, app_id={app_id!r}")
        name = re.sub(r"[^A-Za-z0-9_.-]", "-", app_id)
        return f"proton-vpn-app-{uid}-{name}.scope"

    def launch(
        self,
        uid: int,
        unit: str,
        executable: str,
        arguments: Sequence[str] = (),
        environment: Mapping[str, str] | None = None,
    ) -> ScopeLaunch:
        """Launch an application in a transient scope and return its process."""
        command = self.command(uid, unit, executable, arguments)
        launch_environment = dict(self._environ)
        if environment:
            launch_environment.update(environment)
        process = self._runner(command, env=launch_environment, start_new_session=True)
        return ScopeLaunch(
            uid=uid,
            unit=unit,
            command=tuple(command),
            pid=process.pid,
        )

    def launch_app(self, uid: int, unit: str, app_id: str) -> ScopeLaunch:
        """Resolve a desktop entry and launch its declared command in a scope."""
        desktop_command = self._desktop_entries.resolve(app_id)
        return self.launch(uid, unit, desktop_command.argv[0], desktop_command.argv[1:])

    def restart(self, previous: ScopeLaunch) -> ScopeLaunch:
        """Restart a stopped command in the same dedicated scope unit."""
        try:
            quiet_index = previous.command.index("--quiet")
            executable, *arguments = previous.command[quiet_index + 1 :]
        except (ValueError, IndexError) as error:
            raise ValueError("Invalid managed scope launch") from error
        return self.launch(previous.uid, previous.unit, executable, arguments)

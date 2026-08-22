"""Resolve application IDs to desktop-entry launch commands."""

from __future__ import annotations

import os
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class DesktopCommand:
    """Executable command declared by a desktop entry."""

    app_id: str
    desktop_file: Path
    argv: tuple[str, ...]


class DesktopEntryResolver:
    """Resolve desktop IDs using the XDG application search paths."""

    def __init__(self, search_paths: Iterable[Path] | None = None):
        self._search_paths = tuple(search_paths or self._default_search_paths())

    @staticmethod
    def _default_search_paths() -> tuple[Path, ...]:
        data_home = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
        data_dirs = os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":")
        return tuple(Path(path) / "applications" for path in (str(data_home), *data_dirs) if path)

    def resolve(self, app_id: str) -> DesktopCommand:
        """Resolve an app ID to its sanitized Exec command."""
        desktop_file = self._find(app_id)
        exec_line = self._desktop_exec(desktop_file)
        argv = self._sanitize_exec(exec_line)
        return DesktopCommand(app_id=app_id, desktop_file=desktop_file, argv=tuple(argv))


    def _find(self, app_id: str) -> Path:
        filename = app_id if app_id.endswith(".desktop") else f"{app_id}.desktop"
        if Path(filename).name != filename:
            raise ValueError(f"Invalid desktop application ID: {app_id!r}")
        for directory in self._search_paths:
            candidate = directory / filename
            if candidate.is_file():
                return candidate
        raise FileNotFoundError(f"Desktop entry not found for {app_id!r}")

    @staticmethod
    def _desktop_exec(path: Path) -> str:
        in_entry = False
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line == "[Desktop Entry]":
                in_entry = True
                continue
            if in_entry and line.startswith("["):
                break
            if in_entry and line.startswith("Exec="):
                return line.removeprefix("Exec=").strip()
        raise ValueError(f"Desktop entry has no application Exec: {path}")

    @staticmethod
    def _sanitize_exec(exec_line: str) -> list[str]:
        argv = shlex.split(exec_line)
        if not argv or argv[0].startswith("-"):
            raise ValueError("Desktop entry has an invalid Exec command")
        sanitized = [argv[0]]
        for argument in argv[1:]:
            if argument in {"%U", "%u", "%F", "%f", "%D", "%d", "%N", "%n", "%i", "%c", "%k"}:
                continue
            if "%" in argument:
                raise ValueError(f"Unsupported desktop Exec field code: {argument!r}")
            sanitized.append(argument)
        if "/" not in sanitized[0] and shutil.which(sanitized[0]) is None:
            raise FileNotFoundError(f"Desktop executable not found: {sanitized[0]!r}")
        return sanitized

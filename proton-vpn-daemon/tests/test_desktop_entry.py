from pathlib import Path

import pytest
from proton.vpn.daemon.split_tunneling.apps.desktop_entry import DesktopEntryResolver


def test_resolves_native_exec_and_removes_field_codes(tmp_path):
    desktop = tmp_path / "applications" / "example.desktop"
    desktop.parent.mkdir()
    desktop.write_text("[Desktop Entry]\nType=Application\nExec=/usr/bin/example --new %U\n", encoding="utf-8")

    command = DesktopEntryResolver([desktop.parent]).resolve("example.desktop")

    assert command.argv == ("/usr/bin/example", "--new")


def test_preserves_flatpak_launcher_command(tmp_path):
    desktop = tmp_path / "applications" / "com.example.App.desktop"
    desktop.parent.mkdir()
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nExec=/usr/bin/flatpak run --file-forwarding com.example.App @@u %u @@\n",
        encoding="utf-8",
    )

    command = DesktopEntryResolver([desktop.parent]).resolve("com.example.App")

    assert command.argv == (
        "/usr/bin/flatpak", "run", "--file-forwarding", "com.example.App", "@@u", "@@"
    )


def test_rejects_unsupported_field_codes(tmp_path):
    desktop = tmp_path / "applications" / "example.desktop"
    desktop.parent.mkdir()
    desktop.write_text("[Desktop Entry]\nExec=/usr/bin/example %Z\n", encoding="utf-8")

    with pytest.raises(ValueError):
        DesktopEntryResolver([desktop.parent]).resolve("example.desktop")


def test_missing_entry_is_explicit(tmp_path):
    with pytest.raises(FileNotFoundError):
        DesktopEntryResolver([tmp_path]).resolve("missing")


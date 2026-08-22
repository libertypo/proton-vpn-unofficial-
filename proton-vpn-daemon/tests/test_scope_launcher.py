from unittest.mock import Mock

import pytest
from proton.vpn.daemon.split_tunneling.apps.scope_launcher import ScopeLauncher


def test_command_builds_user_scope_launch():
    launcher = ScopeLauncher()

    assert launcher.command(1000, "app-example-1.scope", "/usr/bin/example", ["--private"]) == [
        "systemd-run", "--user", "--machine", "1000@.host", "--scope", "--uid", "1000",
        "--unit", "app-example-1.scope", "--quiet", "/usr/bin/example", "--private",
    ]


def test_launch_returns_scope_description_and_passes_environment():
    process = Mock(pid=4242)
    runner = Mock(return_value=process)
    launcher = ScopeLauncher(runner=runner, environ={"DISPLAY": ":0"})

    launch = launcher.launch(1000, "app-example-1.scope", "/usr/bin/example", environment={"LANG": "C"})

    assert launch.pid == 4242
    assert launch.uid == 1000
    assert launch.unit == "app-example-1.scope"
    runner.assert_called_once_with(
        [
            "systemd-run", "--user", "--machine", "1000@.host", "--scope",
            "--uid", "1000", "--unit", "app-example-1.scope", "--quiet", "/usr/bin/example",
        ],
        env={"DISPLAY": ":0", "LANG": "C"},
        start_new_session=True,
    )


@pytest.mark.parametrize("unit", ["example.service", "../x.scope", "x/y.scope"])
def test_command_rejects_non_scope_units(unit):
    with pytest.raises(ValueError):
        ScopeLauncher().command(1000, unit, "/usr/bin/example")


def test_command_rejects_invalid_user_or_executable():
    launcher = ScopeLauncher()

    with pytest.raises(ValueError):
        launcher.command(-1, "app-example.scope", "/usr/bin/example")
    with pytest.raises(ValueError):
        launcher.command(1000, "app-example.scope", "-bad")


def test_unit_for_app_generates_a_dedicated_user_scope():
    assert ScopeLauncher.unit_for_app(1000, "com.example.App") == (
        "proton-vpn-app-1000-com.example.App.scope"
    )


def test_unit_for_app_rejects_invalid_identity():
    with pytest.raises(ValueError):
        ScopeLauncher.unit_for_app(1000, "../example")


def test_launch_app_resolves_desktop_command_and_uses_requested_scope():
    desktop_entries = Mock()
    desktop_entries.resolve.return_value.argv = ("/usr/bin/flatpak", "run", "com.example.App")
    process = Mock(pid=4242)
    launcher = ScopeLauncher(runner=Mock(return_value=process), desktop_entries=desktop_entries)

    launch = launcher.launch_app(1000, "proton-vpn-app-example.scope", "com.example.App")

    desktop_entries.resolve.assert_called_once_with("com.example.App")
    assert launch.command[-3:] == ("/usr/bin/flatpak", "run", "com.example.App")


def test_restart_reuses_the_managed_scope_unit_and_command():
    process = Mock(pid=5252)
    runner = Mock(return_value=process)
    launcher = ScopeLauncher(runner=runner)
    previous = launcher.launch(1000, "proton-vpn-app.scope", "/usr/bin/example", ["--helper"])

    restarted = launcher.restart(previous)

    assert restarted.unit == previous.unit
    assert restarted.command == previous.command
    assert runner.call_count == 2

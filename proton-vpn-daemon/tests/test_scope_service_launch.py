from unittest.mock import Mock, patch

import pytest

from proton.vpn.core.settings import SplitTunnelingConfig, SplitTunnelingMode
from proton.vpn.daemon.split_tunneling.apps.cgroup_manager import (
    CgroupDiscoveryUnavailable,
    CgroupIdentity,
)
from proton.vpn.daemon.split_tunneling.apps.service import (
    AlreadyRunningApplication,
    AppBasedSplitTunnelingService,
)
from proton.vpn.daemon.split_tunneling.apps.scope_launcher import ScopeLaunch


def test_launch_selected_registers_dedicated_scope_policy():
    with patch("proton.vpn.daemon.split_tunneling.apps.service.SocketMonitor") as monitor_type:
        service = AppBasedSplitTunnelingService()
    service._socket_monitor = monitor_type.return_value
    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE, app_paths=[])
    config.app_ids = ["com.example.App"]
    service._cgroup_config = {1000: config}
    service._scope_launcher = Mock()
    service._scope_launcher.launch_app.return_value = ScopeLaunch(
        uid=1000,
        unit="proton-vpn-example.scope",
        command=("systemd-run",),
        pid=4242,
    )
    identity = CgroupIdentity(
        path=service._cgroup_manager._cgroup_root,
        inode=service._cgroup_manager._cgroup_root.stat().st_ino,
    )
    service._cgroup_manager.identities_for_app_id = Mock(side_effect=[(), (identity,)])
    service._cgroup_manager.wait_for_unit = Mock(return_value=identity)
    service._cgroup_manager.prune_missing = Mock(return_value=frozenset())

    launch = service.launch_selected(1000, "com.example.App", "proton-vpn-example.scope")

    assert launch.pid == 4242
    service._scope_launcher.launch_app.assert_called_once_with(
        1000, "proton-vpn-example.scope", "com.example.App"
    )
    assert service._cgroup_manager.selected_inodes == frozenset({identity.inode})
    service._socket_monitor.replace_cgroups.assert_called_once_with({identity.inode})


def test_launch_selected_rejects_an_existing_application_scope():
    with patch("proton.vpn.daemon.split_tunneling.apps.service.SocketMonitor") as monitor_type:
        service = AppBasedSplitTunnelingService()
    service._socket_monitor = monitor_type.return_value
    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE, app_paths=[])
    config.app_ids = ["com.example.App"]
    service._cgroup_config = {1000: config}
    existing = CgroupIdentity(path=service._cgroup_manager._cgroup_root / "app-existing.scope", inode=1234)
    service._cgroup_manager.identities_for_app_id = Mock(return_value=(existing,))
    service._scope_launcher = Mock()

    with pytest.raises(AlreadyRunningApplication) as error:
        service.launch_selected(1000, "com.example.App")

    assert "app-existing.scope" in str(error.value)
    service._scope_launcher.launch_app.assert_not_called()


def test_adopt_selected_requires_and_tracks_exact_scope_unit():
    with patch("proton.vpn.daemon.split_tunneling.apps.service.SocketMonitor") as monitor_type:
        service = AppBasedSplitTunnelingService()
    service._socket_monitor = monitor_type.return_value
    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE, app_paths=[])
    config.app_ids = ["com.example.App"]
    service._cgroup_config = {1000: config}
    identity = CgroupIdentity(path=service._cgroup_manager._cgroup_root, inode=9876)
    service._cgroup_manager.identity_for_unit = Mock(return_value=identity)
    service.sync_cgroup_policies = Mock()

    adopted = service.adopt_selected(1000, "com.example.App", "app-existing.scope")

    assert adopted == identity
    service._cgroup_manager.identity_for_unit.assert_called_once_with(
        1000, "app-existing.scope"
    )
    assert service._cgroup_manager.selected_inodes == frozenset({9876})
    service.sync_cgroup_policies.assert_called_once()


def test_restart_selected_reuses_scope_and_updates_policy():
    with patch("proton.vpn.daemon.split_tunneling.apps.service.SocketMonitor") as monitor_type:
        service = AppBasedSplitTunnelingService()
    service._socket_monitor = monitor_type.return_value
    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE, app_paths=[])
    config.app_ids = ["com.example.App"]
    service._cgroup_config = {1000: config}
    previous = ScopeLaunch(
        uid=1000,
        unit="proton-vpn-example.scope",
        command=("systemd-run", "--quiet", "/usr/bin/example"),
        pid=4242,
    )
    identity = CgroupIdentity(
        path=service._cgroup_manager._cgroup_root,
        inode=service._cgroup_manager._cgroup_root.stat().st_ino,
    )
    service._cgroup_manager.identity_for_unit = Mock(
        side_effect=CgroupDiscoveryUnavailable("scope has stopped")
    )
    service._cgroup_manager.wait_for_unit = Mock(return_value=identity)
    service._cgroup_manager.prune_missing = Mock(return_value=frozenset())
    service._scope_launcher = Mock()
    service._scope_launcher.restart.return_value = previous
    service.sync_cgroup_policies = Mock()

    restarted = service.restart_selected(1000, "com.example.App", previous)

    assert restarted == previous
    service._scope_launcher.restart.assert_called_once_with(previous)
    service._cgroup_manager.wait_for_unit.assert_called_once_with(
        1000, "proton-vpn-example.scope"
    )
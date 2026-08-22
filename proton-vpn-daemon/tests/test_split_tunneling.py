import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
from proton.vpn.daemon.split_tunneling.apps.cgroup_manager import CgroupManager, DnsPolicy
from proton.vpn.daemon.split_tunneling.apps.dns_proxy import DnsProxy
from proton.vpn.daemon.split_tunneling.apps.service import AppBasedSplitTunnelingService as AppService
from proton.vpn.daemon.split_tunneling.split_tunneling import SplitTunnelingService

from proton.vpn.core.settings import SplitTunnelingConfig, SplitTunnelingMode


@pytest.mark.asyncio
async def test_get_config_returns_existing_config():
    config_by_uid = {
        1000: SplitTunnelingConfig()
    }
    sut = SplitTunnelingService(
        config_by_uid=config_by_uid,
        app_service=AsyncMock(spec=AppService)
    )

    assert(sut.get_config(1000) == config_by_uid[1000])


def test_get_config_returns_none_if_config_was_not_set():
    sut = SplitTunnelingService(app_service=AsyncMock(spec=AppService))
    assert sut.get_config(1000) is None


def test_sync_cgroup_policies_reconciles_excluded_app_ids():
    sut = AppService.__new__(AppService)
    sut._cgroup_manager = Mock()
    sut._socket_monitor = Mock()
    sut._cgroup_manager.selected_inodes = frozenset({1})
    sut._cgroup_manager.tracked_app_ids = frozenset()
    sut._cgroup_manager.reconcile_app_id.return_value = ()
    sut._cgroup_manager.prune_missing.return_value = frozenset()

    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE)
    config.app_ids = ["com.example.App"]
    sut.sync_cgroup_policies({1000: config})

    sut._cgroup_manager.reconcile_app_id.assert_called_once_with("com.example.App")


@pytest.mark.asyncio
async def test_app_id_start_uses_cgroup_discovery_without_process_monitor():
    sut = AppService.__new__(AppService)
    sut._socket_monitor = Mock()
    sut._cgroup_reconcile_task = None
    sut._cgroup_config = {}
    sut.sync_cgroup_policies = Mock()
    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE)
    config.app_ids = ["com.example.App"]

    sut.start({1000: config})
    await asyncio.sleep(0)

    sut.sync_cgroup_policies.assert_called_once_with({1000: config})
    await sut.stop()


@pytest.mark.asyncio
async def test_app_id_start_creates_reconciliation_task():
    sut = AppService.__new__(AppService)
    sut._process_monitor = Mock()
    sut._process_monitor.stop = AsyncMock()
    sut._socket_monitor = Mock()
    sut._cgroup_only = False
    sut._cgroup_reconcile_task = None
    sut._cgroup_config = {}
    sut.sync_cgroup_policies = Mock()
    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE)
    config.app_ids = ["com.example.App"]

    task = sut.start({1000: config})

    assert task is sut._cgroup_reconcile_task
    assert sut._cgroup_config == {1000: config}
    await sut.stop()


def test_sync_cgroup_policies_replaces_exclude_with_include_scope_set(tmp_path):
    excluded = (
        tmp_path / "user.slice" / "user-1000.slice" / "app-com.example.Excluded-1.scope"
    )
    other = (
        tmp_path / "user.slice" / "user-1000.slice" / "app-com.example.Other-2.scope"
    )
    excluded.mkdir(parents=True)
    other.mkdir(parents=True)

    sut = AppService.__new__(AppService)
    sut._cgroup_manager = CgroupManager(tmp_path)
    sut._socket_monitor = Mock()

    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE)
    config.app_ids = ["com.example.Excluded"]
    sut.sync_cgroup_policies({1000: config})
    excluded_inode = excluded.stat().st_ino
    assert sut._socket_monitor.replace_cgroups.call_args.args[0] == {excluded_inode}

    config.mode = SplitTunnelingMode.INCLUDE
    sut.sync_cgroup_policies({1000: config})

    assert sut._socket_monitor.replace_cgroups.call_args.args[0] == {other.stat().st_ino}
    assert sut._cgroup_manager.dns_policies == {
        excluded.stat().st_ino: DnsPolicy.VPN,
        other.stat().st_ino: DnsPolicy.PHYSICAL,
    }


def test_sync_cgroup_policies_updates_dns_proxy_without_global_dns_mutation(tmp_path):
    excluded = (
        tmp_path / "user.slice" / "user-1000.slice" / "app-com.example.Excluded-1.scope"
    )
    excluded.mkdir(parents=True)
    dns_proxy = Mock(spec=DnsProxy)
    sut = AppService.__new__(AppService)
    sut._cgroup_manager = CgroupManager(tmp_path)
    sut._socket_monitor = Mock()
    sut._dns_proxy = dns_proxy

    config = SplitTunnelingConfig(mode=SplitTunnelingMode.EXCLUDE)
    config.app_ids = ["com.example.Excluded"]
    sut.sync_cgroup_policies({1000: config})

    dns_proxy.replace_policies.assert_called_once_with(
        {excluded.stat().st_ino: DnsPolicy.PHYSICAL}
    )


@pytest.mark.asyncio
async def test_set_config_starts_app_service_if_app_based_config():
    app_service = AsyncMock(spec=AppService)
    sut = SplitTunnelingService(app_service=app_service)

    config = SplitTunnelingConfig(app_paths=["/usr/bin/app"])
    await sut.set_config(1000, config)

    app_service.start.assert_called_once_with({1000: config})


@pytest.mark.asyncio
async def test_app_id_config_is_app_based_without_legacy_path():
    app_service = AsyncMock(spec=AppService)
    sut = SplitTunnelingService(app_service=app_service)

    config = SplitTunnelingConfig()
    config.app_ids = ["com.example.App"]
    await sut.set_config(1000, config)

    app_service.start.assert_called_once_with({1000: config})

@pytest.mark.asyncio
async def test_set_config_stops_app_service_if_not_app_based_config():
    app_service = AsyncMock(spec=AppService)
    sut = SplitTunnelingService(app_service=app_service)

    config = SplitTunnelingConfig(app_paths=[])
    await sut.set_config(1000, config)

    app_service.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_clear_config_starts_app_service_if_remaining_app_based_config():
    config_by_uid = {
        1000: SplitTunnelingConfig(app_paths=["/usr/bin/app"]),
        1001: SplitTunnelingConfig(app_paths=["/usr/bin/app2"])
    }
    app_service = AsyncMock(spec=AppService)
    sut = SplitTunnelingService(
        config_by_uid=config_by_uid,
        app_service=app_service
    )

    await sut.clear_config(1000)

    app_service.start.assert_called_once_with({1001: SplitTunnelingConfig(app_paths=["/usr/bin/app2"])})

@pytest.mark.asyncio
async def test_clear_config_stops_app_service_if_no_remaining_app_based_config():
    config_by_uid = {
        1000: SplitTunnelingConfig(app_paths=["/usr/bin/app"])
    }
    app_service = AsyncMock(spec=AppService)
    sut = SplitTunnelingService(
        config_by_uid=config_by_uid,
        app_service=app_service
    )

    await sut.clear_config(1000)

    app_service.stop.assert_called_once()

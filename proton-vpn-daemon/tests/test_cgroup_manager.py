import pytest
from proton.vpn.daemon.split_tunneling.apps.cgroup_manager import (
    CgroupDiscoveryUnavailable,
    CgroupManager,
    DnsPolicy,
)


def test_identities_for_app_id_finds_native_and_flatpak_scopes(tmp_path):
    native = tmp_path / "user.slice" / "app-com.vivaldi.Vivaldi-123.scope"
    flatpak = tmp_path / "user.slice" / "app-flatpak-com.rtosta.zapzap-456.scope"
    native.mkdir(parents=True)
    flatpak.mkdir(parents=True)

    identities = CgroupManager(tmp_path).identities_for_app_id("com.vivaldi.Vivaldi")

    assert [identity.path for identity in identities] == [native]

    identities = CgroupManager(tmp_path).identities_for_app_id("com.rtosta.zapzap")

    assert [identity.path for identity in identities] == [flatpak]


def test_identities_for_app_id_finds_snap_scope(tmp_path):
    snap = tmp_path / "user.slice" / "app-snap-example.Snap-789.scope"
    snap.mkdir(parents=True)

    identities = CgroupManager(tmp_path).identities_for_app_id("example.Snap")

    assert [identity.path for identity in identities] == [snap]


def test_identities_for_app_id_matches_desktop_entry_systemd_scope(tmp_path):
    scope = tmp_path / "user.slice" / "app-gnome-microsoft\\x2dedge\\x2dbeta-789.scope"
    scope.mkdir(parents=True)

    identities = CgroupManager(tmp_path).identities_for_app_id("microsoft-edge-beta.desktop")

    assert [identity.path for identity in identities] == [scope]


def test_missing_application_scope_is_pending_until_app_starts(tmp_path):
    assert CgroupManager(tmp_path).identities_for_app_id("com.example.NotRunning") == ()


def test_scope_discovery_reports_unsupported_host(tmp_path):
    unavailable = tmp_path / "missing-cgroup-root"

    with pytest.raises(CgroupDiscoveryUnavailable):
        CgroupManager(unavailable).identities_for_app_id("com.example.App")


def test_identities_for_app_id_rejects_path_traversal(tmp_path):
    try:
        CgroupManager(tmp_path).identities_for_app_id("../user.slice")
    except ValueError:
        pass
    else:
        raise AssertionError("path traversal application ID was accepted")


def test_identities_for_uid_finds_application_scopes(tmp_path):
    scope = (
        tmp_path
        / "user.slice"
        / "user-1000.slice"
        / "app.slice"
        / "app-example-123.scope"
    )
    scope.mkdir(parents=True)

    identities = CgroupManager(tmp_path).identities_for_uid(1000)

    assert [identity.path for identity in identities] == [scope]


def test_reconcile_app_id_replaces_restarted_scope(tmp_path):
    old_scope = tmp_path / "app-com.example.App-123.scope"
    old_scope.mkdir(parents=True)
    manager = CgroupManager(tmp_path)

    first = manager.reconcile_app_id("com.example.App")
    old_inode = first[0].inode

    old_scope.rmdir()
    (tmp_path / "app-com.example.App-456.scope").mkdir(parents=True)
    second = manager.reconcile_app_id("com.example.App")

    assert second[0].inode != old_inode
    assert manager.selected_inodes == frozenset({second[0].inode})


def test_prune_missing_removes_disappeared_scope(tmp_path):
    scope = tmp_path / "app-com.example.App-123.scope"
    scope.mkdir(parents=True)
    manager = CgroupManager(tmp_path)
    identity = manager.reconcile_app_id("com.example.App")[0]

    scope.rmdir()

    assert manager.prune_missing() == frozenset({identity.inode})
    assert manager.selected_inodes == frozenset()


def test_dns_policies_are_explicit_and_pruned_with_missing_cgroups(tmp_path):
    scope = tmp_path / "app-com.example.App-123.scope"
    scope.mkdir(parents=True)
    manager = CgroupManager(tmp_path)
    inode = scope.stat().st_ino

    manager.replace_dns_policies({inode: DnsPolicy.PHYSICAL})
    assert manager.dns_policies == {inode: DnsPolicy.PHYSICAL}

    scope.rmdir()
    manager.prune_missing()

    assert manager.dns_policies == {}


def test_identity_for_unit_and_explicit_tracking_survive_reconciliation(tmp_path):
    scope = (
        tmp_path
        / "user.slice"
        / "user-1000.slice"
        / "app.slice"
        / "proton-vpn-example.scope"
    )
    scope.mkdir(parents=True)
    manager = CgroupManager(tmp_path)
    identity = manager.identity_for_unit(1000, "proton-vpn-example.scope")

    manager.track_identity("com.example.App", identity)

    assert manager.reconcile_app_id("com.example.App") == (identity,)
    assert manager.selected_inodes == frozenset({identity.inode})




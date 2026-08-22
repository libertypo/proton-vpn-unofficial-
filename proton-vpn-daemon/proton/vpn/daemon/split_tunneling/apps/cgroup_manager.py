"""Utilities for resolving application cgroup identities."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

CGROUP_ROOT = Path("/sys/fs/cgroup")
APPLICATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class CgroupDiscoveryUnavailable(RuntimeError):
    """The host cannot provide application-scope cgroup discovery."""


class DnsPolicy(Enum):
    """Resolver route intended for one application cgroup."""

    VPN = "vpn"
    PHYSICAL = "physical"


@dataclass(frozen=True)
class CgroupIdentity:
    """Kernel and filesystem identity for one cgroup v2 directory."""

    path: Path
    inode: int


class CgroupManager:
    """Resolve process membership in the host cgroup v2 hierarchy."""

    def __init__(self, cgroup_root: Path = CGROUP_ROOT):
        self._cgroup_root = cgroup_root
        self._selected_inodes: set[int] = set()
        self._app_to_inodes: dict[str, set[int]] = {}
        self._dns_policies: dict[int, DnsPolicy] = {}

    def identities_for_app_id(self, app_id: str) -> tuple[CgroupIdentity, ...]:
        """Find active native or Flatpak systemd application scopes by app ID."""
        if not APPLICATION_ID_PATTERN.fullmatch(app_id):
            raise ValueError(f"Invalid application ID: {app_id!r}")
        if not self._cgroup_root.is_dir():
            raise CgroupDiscoveryUnavailable(
                f"Application scope discovery is unavailable: {self._cgroup_root}"
            )

        identities = []
        for scope in self._cgroup_root.rglob("*.scope"):
            if self._scope_matches_app_id(scope.name, app_id) and scope.is_dir():
                identities.append(CgroupIdentity(path=scope, inode=scope.stat().st_ino))
        return tuple(sorted(identities, key=lambda identity: str(identity.path)))

    @staticmethod
    def _scope_matches_app_id(scope_name: str, app_id: str) -> bool:
        """Match desktop-entry IDs against systemd escaped scope names."""
        scope_stem = scope_name.removesuffix(".scope")
        scope_stem = re.sub(r"-\d+$", "", scope_stem)
        scope_stem = re.sub(r"^app-(?:flatpak-|snap-|gnome-)?", "", scope_stem)
        scope_stem = scope_stem.replace(r"\x2d", "-").replace(r"\x2e", ".")
        normalized_scope = re.sub(r"[^a-z0-9]", "", scope_stem.lower())
        normalized_id = re.sub(r"\.desktop$", "", app_id.lower())
        normalized_id = re.sub(r"[^a-z0-9]", "", normalized_id)
        return normalized_scope == normalized_id

    def identities_for_uid(self, uid: int) -> tuple[CgroupIdentity, ...]:
        """Return application scopes belonging to a user session."""
        if uid < 0:
            raise ValueError(f"Invalid user ID: {uid}")
        user_root = self._cgroup_root / "user.slice" / f"user-{uid}.slice"
        return tuple(
            CgroupIdentity(path=scope, inode=scope.stat().st_ino)
            for scope in sorted(user_root.rglob("*.scope"))
            if scope.is_dir()
        )

    def identity_for_unit(self, uid: int, unit: str) -> CgroupIdentity:
        """Resolve a daemon-owned scope unit in a user's cgroup subtree."""
        if uid < 0 or not unit.endswith(".scope") or "/" in unit or ".." in unit:
            raise ValueError(f"Invalid scope identity: uid={uid}, unit={unit!r}")
        user_root = self._cgroup_root / "user.slice" / f"user-{uid}.slice"
        matches = [path for path in user_root.rglob(unit) if path.is_dir()]
        if len(matches) != 1:
            raise CgroupDiscoveryUnavailable(
                f"Scope unit is not available for UID {uid}: {unit}"
            )
        scope = matches[0]
        return CgroupIdentity(path=scope, inode=scope.stat().st_ino)

    def wait_for_unit(
        self, uid: int, unit: str, timeout: float = 5.0, poll_interval: float = 0.05
    ) -> CgroupIdentity:
        """Wait for a transient scope to appear after systemd-run starts it."""
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self.identity_for_unit(uid, unit)
            except CgroupDiscoveryUnavailable:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(poll_interval)

    @property
    def selected_inodes(self) -> frozenset[int]:
        """Return the cgroup identities currently selected for bypass."""
        return frozenset(self._selected_inodes)

    @property
    def tracked_app_ids(self) -> frozenset[str]:
        """Return application IDs with managed scope policies."""
        return frozenset(self._app_to_inodes)

    @property
    def dns_policies(self) -> dict[int, DnsPolicy]:
        """Return explicit DNS policy by cgroup inode."""
        return dict(self._dns_policies)

    def replace_dns_policies(self, policies: dict[int, DnsPolicy]):
        """Atomically replace the explicit per-cgroup DNS policy set."""
        self._dns_policies = dict(policies)

    def reconcile_app_id(self, app_id: str) -> tuple[CgroupIdentity, ...]:
        """Refresh the selected scopes for an app after start or scope restart."""
        identities = self.identities_for_app_id(app_id)
        old_inodes = self._app_to_inodes.get(app_id, set())
        new_inodes = {identity.inode for identity in identities}
        if not identities:
            preserved = {
                inode: self._path_for_inode(inode)
                for inode in old_inodes
                if self._path_exists_for_inode(inode)
            }
            identities = tuple(
                CgroupIdentity(path=path, inode=inode)
                for inode, path in preserved.items()
            )
            new_inodes.update(preserved)
        self._app_to_inodes[app_id] = new_inodes
        self._selected_inodes.difference_update(old_inodes - new_inodes)
        self._selected_inodes.update(new_inodes)
        return identities

    def track_identity(self, app_id: str, identity: CgroupIdentity):
        """Track an explicitly launched scope for app-policy reconciliation."""
        self._app_to_inodes.setdefault(app_id, set()).add(identity.inode)
        self._selected_inodes.add(identity.inode)

    def remove_app_id(self, app_id: str):
        """Remove an application's scope policy while retaining shared scopes."""
        self._app_to_inodes.pop(app_id, None)
        self._recompute_selected_inodes()

    def prune_missing(self) -> frozenset[int]:
        """Remove selected cgroup identities whose directories no longer exist."""
        missing = {
            inode for inode in self._selected_inodes
            if not self._path_exists_for_inode(inode)
        }
        missing.update(
            inode for inode in self._dns_policies if not self._path_exists_for_inode(inode)
        )
        for app_id in self._app_to_inodes:
            self._app_to_inodes[app_id].difference_update(missing)
        self._dns_policies = {
            inode: policy
            for inode, policy in self._dns_policies.items()
            if inode not in missing
        }
        self._recompute_selected_inodes()
        return frozenset(missing)

    def _recompute_selected_inodes(self):
        self._selected_inodes = set()
        for app_inodes in self._app_to_inodes.values():
            self._selected_inodes.update(app_inodes)

    def _path_for_inode(self, inode: int) -> Path:
        for path in self._cgroup_root.rglob("*"):
            if path.is_dir() and path.stat().st_ino == inode:
                return path
        raise RuntimeError(f"Cgroup inode {inode} no longer exists")

    def _path_exists_for_inode(self, inode: int) -> bool:
        try:
            self._path_for_inode(inode)
        except RuntimeError:
            return False
        return True

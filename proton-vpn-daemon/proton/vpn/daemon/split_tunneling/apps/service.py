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
from dataclasses import dataclass
from typing import Awaitable

from proton.vpn.daemon.split_tunneling.apps.cgroup_manager import (
    CgroupDiscoveryUnavailable,
    CgroupIdentity,
    CgroupManager,
    DnsPolicy,
)
from proton.vpn.daemon.split_tunneling.apps.dns_proxy import DnsProxy
from proton.vpn.daemon.split_tunneling.apps.scope_launcher import ScopeLaunch, ScopeLauncher
from proton.vpn.daemon.split_tunneling.apps.socket_monitor import SocketMonitor

from proton.vpn import logging
from proton.vpn.core.settings import SplitTunnelingConfig, SplitTunnelingMode

logger = logging.getLogger(__name__)


class AlreadyRunningApplication(RuntimeError):
    """An application already has one or more active scopes."""

    def __init__(self, uid: int, app_id: str, units: tuple[str, ...]):
        self.uid = uid
        self.app_id = app_id
        self.units = units
        super().__init__(
            f"Application is already running for UID {uid}: {app_id} "
            f"({', '.join(units)})"
        )


@dataclass
class ManagedApp:
    """Runtime launch/adoption state for one selected application."""

    state: str
    unit: str
    pid: int
    cgroup_inode: int


class AppBasedSplitTunnelingService:
    """Service to split-tunnel applications."""

    def __init__(self, dns_proxy: DnsProxy | None = None):
        self._cgroup_manager = CgroupManager()
        self._socket_monitor = SocketMonitor()
        self._scope_launcher = ScopeLauncher()
        self._dns_proxy = dns_proxy
        self._cgroup_reconcile_task: asyncio.Task | None = None
        self._cgroup_config: dict[int, SplitTunnelingConfig] = {}
        self._managed_apps: dict[tuple[int, str], ManagedApp] = {}

    def log_status(self):
        """Logs the service status."""
        logger.info("==============App-based ST service status============")
        logger.info("Tracked cgroups: %s", self._cgroup_manager.selected_inodes)
        self._socket_monitor.log_status()
        logger.info("=====================================================")

    def sync_cgroup_policies(self, config_by_uid: dict[int, SplitTunnelingConfig]):
        """Reconcile app-ID scope policies with the socket monitor."""
        desired_inodes: set[int] = set()
        desired_dns_policies: dict[int, DnsPolicy] = {}
        configured_ids = set()
        for uid, config in config_by_uid.items():
            app_ids = getattr(config, "app_ids", [])
            logger.info("Cgroup app policy for UID %s: app_ids=%s", uid, app_ids)
            configured_ids.update(app_ids)
            selected = {
                identity.inode
                for app_id in app_ids
                for identity in self._cgroup_manager.reconcile_app_id(app_id)
            }
            if config.mode == SplitTunnelingMode.EXCLUDE:
                desired_inodes.update(selected)
                desired_dns_policies.update(
                    {inode: DnsPolicy.PHYSICAL for inode in selected}
                )
            elif config.mode == SplitTunnelingMode.INCLUDE:
                uid_identities = self._cgroup_manager.identities_for_uid(uid)
                desired_inodes.update(
                    identity.inode for identity in uid_identities if identity.inode not in selected
                )
                desired_dns_policies.update(
                    {
                        identity.inode: (
                            DnsPolicy.VPN
                            if identity.inode in selected
                            else DnsPolicy.PHYSICAL
                        )
                        for identity in uid_identities
                    }
                )

            logger.info("Resolved cgroup inodes for UID %s: %s", uid, sorted(selected))

        for app_id in self._cgroup_manager.tracked_app_ids - configured_ids:
            self._cgroup_manager.remove_app_id(app_id)
        self._cgroup_manager.prune_missing()
        self._cgroup_manager.replace_dns_policies(desired_dns_policies)
        dns_proxy = getattr(self, "_dns_proxy", None)
        if dns_proxy is not None:
            dns_proxy.replace_policies(desired_dns_policies)
        logger.info("Installing cgroup policy inodes: %s", sorted(desired_inodes))
        self._socket_monitor.replace_cgroups(desired_inodes)

    def launch_selected(self, uid: int, app_id: str, unit: str | None = None) -> ScopeLaunch:
        """Launch a configured application in a dedicated scope and route it."""
        config = self._cgroup_config.get(uid)
        if config is None or app_id not in getattr(config, "app_ids", []):
            raise ValueError(f"Application is not selected for UID {uid}: {app_id}")
        existing = self._cgroup_manager.identities_for_app_id(app_id)
        if existing:
            raise AlreadyRunningApplication(
                uid, app_id, tuple(identity.path.name for identity in existing)
            )
        unit = unit or self._scope_launcher.unit_for_app(uid, app_id)
        launch = self._scope_launcher.launch_app(uid, unit, app_id)
        identity = self._cgroup_manager.wait_for_unit(uid, unit)
        self._cgroup_manager.track_identity(app_id, identity)
        self._managed_apps[(uid, app_id)] = ManagedApp(
            state="launched",
            unit=unit,
            pid=launch.pid,
            cgroup_inode=identity.inode,
        )
        self.sync_cgroup_policies(self._cgroup_config)
        return launch

    def adopt_selected(self, uid: int, app_id: str, unit: str) -> CgroupIdentity:
        """Adopt an already-running app only when its exact scope is supplied."""
        config = self._cgroup_config.get(uid)
        if config is None or app_id not in getattr(config, "app_ids", []):
            raise ValueError(f"Application is not selected for UID {uid}: {app_id}")
        identity = self._cgroup_manager.identity_for_unit(uid, unit)
        self._cgroup_manager.track_identity(app_id, identity)
        self._managed_apps[(uid, app_id)] = ManagedApp(
            state="adopted",
            unit=unit,
            pid=0,
            cgroup_inode=identity.inode,
        )
        self.sync_cgroup_policies(self._cgroup_config)
        return identity

    def restart_selected(self, uid: int, app_id: str, previous: ScopeLaunch) -> ScopeLaunch:
        """Restart a stopped selected app using its existing managed scope unit."""
        config = self._cgroup_config.get(uid)
        if config is None or app_id not in getattr(config, "app_ids", []):
            raise ValueError(f"Application is not selected for UID {uid}: {app_id}")
        if previous.uid != uid:
            raise ValueError("Managed scope belongs to a different user")
        try:
            self._cgroup_manager.identity_for_unit(uid, previous.unit)
        except CgroupDiscoveryUnavailable:
            pass
        else:
            raise AlreadyRunningApplication(uid, app_id, (previous.unit,))
        launch = self._scope_launcher.restart(previous)
        identity = self._cgroup_manager.wait_for_unit(uid, previous.unit)
        self._cgroup_manager.track_identity(app_id, identity)
        self._managed_apps[(uid, app_id)] = ManagedApp(
            state="restarted",
            unit=previous.unit,
            pid=launch.pid,
            cgroup_inode=identity.inode,
        )
        self.sync_cgroup_policies(self._cgroup_config)
        return launch

    def get_launch_status(self, uid: int, app_id: str) -> dict[str, object]:
        """Return stable launch/adoption state for D-Bus reporting."""
        managed = self._managed_apps.get((uid, app_id))
        if managed is not None:
            try:
                self._cgroup_manager.identity_for_unit(uid, managed.unit)
            except CgroupDiscoveryUnavailable:
                return {
                    "state": "stopped",
                    "unit": managed.unit,
                    "pid": managed.pid,
                    "cgroup_inode": managed.cgroup_inode,
                }
            return {
                "state": managed.state,
                "unit": managed.unit,
                "pid": managed.pid,
                "cgroup_inode": managed.cgroup_inode,
            }

        existing = self._cgroup_manager.identities_for_app_id(app_id)
        if existing:
            return {
                "state": "running-unmanaged",
                "unit": existing[0].path.name,
                "pid": 0,
                "cgroup_inode": existing[0].inode,
            }
        return {"state": "not-running", "unit": "", "pid": 0, "cgroup_inode": 0}

    def start(self, config_by_uid: dict[int, SplitTunnelingConfig]) -> Awaitable[None]:
        """
        Starts the service in the background. This method is non-blocking.
        :param config_by_uid: split tunneling configuration indexed by unix user ID.
        :returns: an awaitable to be able to await until the service is stopped.
        """
        self._socket_monitor.start()
        if any(config.app_paths and not getattr(config, "app_ids", []) for config in config_by_uid.values()):
            self._socket_monitor.stop()
            raise ValueError("App-based split tunneling requires stable app_ids")
        self._cgroup_config = config_by_uid
        self.sync_cgroup_policies(config_by_uid)
        if self._cgroup_reconcile_task is None:
            self._cgroup_reconcile_task = asyncio.create_task(
                self._run_cgroup_reconciliation()
            )
        return self._cgroup_reconcile_task

    async def _run_cgroup_reconciliation(self):
        """Refresh scope inodes while app-ID split tunneling is active."""
        try:
            while True:
                await asyncio.sleep(1)
                self.sync_cgroup_policies(self._cgroup_config)
        except asyncio.CancelledError:
            raise

    async def stop(self):
        """Stops the service."""
        if self._cgroup_reconcile_task is not None:
            self._cgroup_reconcile_task.cancel()
            try:
                await self._cgroup_reconcile_task
            except asyncio.CancelledError:
                pass
            self._cgroup_reconcile_task = None
        self._socket_monitor.stop()
        self._cgroup_config = {}

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
import ctypes
import os

from bcc import BPF

from proton.vpn import logging
from proton.vpn.connection import FWMARK_VALUE

logger = logging.getLogger(__name__)


# ebpf program to split traffic based on cgroup identity
BPF_PROGRAM = f"""
BPF_HASH(cgroup_map, u64, u32);

int split_tunnel(struct bpf_sock *sk) {{
    u64 cgroup_id = bpf_get_current_cgroup_id();

    u32 *cgroup_found = cgroup_map.lookup(&cgroup_id);

    if (cgroup_found) {{
        bpf_trace_printk("Excluding cgroup %llu from VPN\\n", cgroup_id);
        sk->mark = {FWMARK_VALUE};
    }}

    return 1;
}}
"""


class SocketMonitor:
    """Split-tunnels sockets based on the proces that creates them."""

    # FIXME could the WG backend somehow pass the interface name (proton0)  # pylint: disable=fixme
    WIREGUARD_INTERFACE_NAME = "proton0"

    def __init__(self):
        self._bpf = BPF(text=BPF_PROGRAM)
        self._bpf_cgroup_map = self._bpf.get_table("cgroup_map")
        self._bpf_split_tunneling_func = self._bpf.load_func(
            "split_tunnel", self._bpf.CGROUP_SOCK
        )
        self._cgroup = None
        self._attached_types = []
        self._bpf_enum_group = None

    def log_status(self):
        """Logs the socket monitor status."""
        logger.info("==============Socket monitor status==================")
        logger.info("Tracked cgroups: %s", [key.value for key in self._bpf_cgroup_map.keys()])
        logger.info("fwmark: %s", FWMARK_VALUE)
        logger.info("=====================================================")

    def start(self):
        """Starts monitoring sockets."""

        if self._started:
            logger.info("Socket monitor already running: fwmark updated to %s",
                        FWMARK_VALUE)
            return

        logger.info("Starting socket monitor (with fwmark %s)", FWMARK_VALUE)
        self._cgroup = os.open("/sys/fs/cgroup/user.slice", os.O_RDONLY)

        attach_type = self._backwards_compatible_bfp_attach_type.CGROUP_INET_SOCK_CREATE
        self._bpf.attach_func(self._bpf_split_tunneling_func, self._cgroup, attach_type)
        self._attached_types.append(attach_type)

    @property
    def _started(self):
        return self._cgroup is not None

    def _cleanup(self):
        self._bpf_cgroup_map.clear()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback):
        self.stop()

    def select_cgroup(self, cgroup_inode: int):
        """
        Select a cgroup for traffic exclusion.
        """
        if not self._started:
            raise RuntimeError("Socket monitor was not started yet")

        logger.debug("Adding %s to cgroup map", cgroup_inode)
        self._bpf_cgroup_map[ctypes.c_uint64(cgroup_inode)] = ctypes.c_uint32(1)

    def unselect_cgroup(self, cgroup_inode: int):
        """
        Stop selecting a cgroup for traffic exclusion.
        """
        if not self._started:
            raise RuntimeError("Socket monitor was not started yet")

        cgroup_c_uint64 = ctypes.c_uint64(cgroup_inode)
        if cgroup_c_uint64 in self._bpf_cgroup_map:
            logger.debug("Removing %s from cgroup map", cgroup_inode)
            del self._bpf_cgroup_map[cgroup_c_uint64]

    def replace_cgroups(self, cgroup_inodes: set[int]):
        """Replace the kernel policy map with the complete desired scope set."""
        if not self._started:
            raise RuntimeError("Socket monitor was not started yet")
        self._bpf_cgroup_map.clear()
        for inode in cgroup_inodes:
            self.select_cgroup(inode)

    def stop(self):
        """Stops monitoring sockets."""
        if not self._started:
            logger.info("Socket monitor is already stopped")
            return

        logger.info("Unloading split tunneling ebpf function")
        for attach_type in self._attached_types:
            try:
                self._bpf.detach_func(
                    self._bpf_split_tunneling_func,
                    self._cgroup,
                    attach_type,
                )
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("BPF hook %s was already detached: %s", attach_type, exc)
        self._attached_types.clear()
        self._cleanup()
        os.close(self._cgroup)
        self._cgroup = None

        logger.info("Socket monitor stopped")

    @property
    def _backwards_compatible_bfp_attach_type(self) -> object:
        """In v20 of bcc a refactor was made where enums
        were extracted into their own type, the BPFAttachType.

        Before that the types were part of the bpf program.

        See more here:
        https://github.com/iovisor/bcc/commit/2731825b9327a9a720f2ef92ed891ce0525a8dc3

        Returns:
            object: Either the BPFAttachType or bpf program.
        """
        if not self._bpf_enum_group:
            try:
                from bcc import BPFAttachType  # pylint: disable=import-outside-toplevel
                self._bpf_enum_group = BPFAttachType
            except ImportError:
                self._bpf_enum_group = self._bpf

        return self._bpf_enum_group

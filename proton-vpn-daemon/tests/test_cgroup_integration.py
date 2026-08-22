import ctypes
import os
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

import pytest
from bcc import BPF, BPFAttachType
from proton.vpn.daemon.split_tunneling.apps.cgroup_manager import (
    CgroupDiscoveryUnavailable,
    CgroupManager,
)
from proton.vpn.daemon.split_tunneling.apps.scope_launcher import ScopeLauncher
from proton.vpn.daemon.split_tunneling.apps.socket_monitor import BPF_PROGRAM

CGROUP_ROOT = Path("/sys/fs/cgroup")
USER_SLICE = CGROUP_ROOT / "user.slice"
ATTACH_TYPE = BPFAttachType.CGROUP_INET_SOCK_CREATE


def _current_cgroup_path() -> Path:
    for line in Path(f"/proc/{os.getpid()}/cgroup").read_text(encoding="utf-8").splitlines():
        hierarchy, controllers, relative_path = line.split(":", 2)
        if hierarchy == "0" and not controllers:
            return CGROUP_ROOT / relative_path.lstrip("/")
    raise RuntimeError("unified cgroup membership not found")


def _cgroup_path_for_pid(pid: int) -> Path:
    for line in Path(f"/proc/{pid}/cgroup").read_text(encoding="utf-8").splitlines():
        hierarchy, controllers, relative_path = line.split(":", 2)
        if hierarchy == "0" and not controllers:
            return CGROUP_ROOT / relative_path.lstrip("/")
    raise RuntimeError(f"unified cgroup membership not found for PID {pid}")


@pytest.fixture
def attached_bpf():
    try:
        bpf = BPF(text=BPF_PROGRAM)
        function = bpf.load_func("split_tunnel", bpf.CGROUP_SOCK)
        cgroup_fd = os.open(USER_SLICE, os.O_RDONLY)
        bpf.attach_func(function, cgroup_fd, ATTACH_TYPE)
    except Exception as error:
        pytest.skip(f"privileged BPF unavailable: {error}")

    try:
        yield bpf
    finally:
        try:
            bpf.detach_func(function, cgroup_fd, ATTACH_TYPE)
        finally:
            os.close(cgroup_fd)


def _set_current_cgroup_policy(bpf):
    cgroup_map = bpf.get_table("cgroup_map")
    cgroup_inode = _current_cgroup_path().stat().st_ino
    cgroup_map[ctypes.c_uint64(cgroup_inode)] = ctypes.c_uint32(1)
    return cgroup_map, cgroup_inode


def test_bpf_attaches_and_detaches_user_slice(attached_bpf):
    _, cgroup_inode = _set_current_cgroup_policy(attached_bpf)
    assert cgroup_inode == _current_cgroup_path().stat().st_ino


def test_selected_cgroup_socket_receives_fwmark(attached_bpf):
    _set_current_cgroup_policy(attached_bpf)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
        assert test_socket.getsockopt(socket.SOL_SOCKET, socket.SO_MARK) != 0


def test_descendant_socket_receives_same_cgroup_policy(attached_bpf):
    _set_current_cgroup_policy(attached_bpf)
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as child_socket:
                mark = child_socket.getsockopt(socket.SOL_SOCKET, socket.SO_MARK)
                os.write(write_fd, str(mark).encode("ascii"))
        finally:
            os._exit(0)

    os.close(write_fd)
    try:
        mark = int(os.read(read_fd, 32))
        _, status = os.waitpid(child_pid, 0)
    finally:
        os.close(read_fd)

    assert os.waitstatus_to_exitcode(status) == 0
    assert mark != 0


def test_multiple_application_scope_identities_are_discoverable(tmp_path):
    first = tmp_path / "user-1000.slice" / "app-com.example.First-1.scope"
    second = tmp_path / "user-1000.slice" / "app-flatpak-com.example.Second-2.scope"
    first.mkdir(parents=True)
    second.mkdir(parents=True)

    manager = CgroupManager(tmp_path)
    first_identity = manager.reconcile_app_id("com.example.First")[0]
    second_identity = manager.reconcile_app_id("com.example.Second")[0]

    assert manager.selected_inodes == {first_identity.inode, second_identity.inode}
    assert first_identity.inode != second_identity.inode


def test_cleanup_removes_policy_before_detach(attached_bpf):
    cgroup_map, cgroup_inode = _set_current_cgroup_policy(attached_bpf)
    del cgroup_map[ctypes.c_uint64(cgroup_inode)]

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as test_socket:
        assert test_socket.getsockopt(socket.SOL_SOCKET, socket.SO_MARK) == 0


def _route_for_socket(marked: bool, bpf) -> str:
    if marked:
        _set_current_cgroup_policy(bpf)

    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as test_socket:
        mark = test_socket.getsockopt(socket.SOL_SOCKET, socket.SO_MARK)

    route = subprocess.run(
        ["ip", "-4", "route", "get", "1.1.1.1", "mark", str(mark)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return mark, route


def test_unmarked_packet_uses_vpn_interface(attached_bpf):
    mark, route = _route_for_socket(marked=False, bpf=attached_bpf)
    assert mark == 0
    assert "dev proton0" in route


def test_marked_packet_uses_physical_interface(attached_bpf):
    mark, route = _route_for_socket(marked=True, bpf=attached_bpf)
    assert mark == 245447468
    assert "dev enp1s0f0" in route


def test_privileged_managed_scope_lifecycle_keeps_helper_and_policy_together(attached_bpf):
    target_uid = int(os.environ.get("SUDO_UID", os.getuid()))
    runtime_directory = Path(f"/run/user/{target_uid}")
    if not runtime_directory.is_dir():
        pytest.skip("user systemd runtime directory is unavailable")

    launch_environment = dict(os.environ)
    launch_environment["XDG_RUNTIME_DIR"] = str(runtime_directory)
    launch_environment.setdefault(
        "DBUS_SESSION_BUS_ADDRESS", f"unix:path={runtime_directory}/bus"
    )
    systemd_probe = subprocess.run(
        ["systemctl", "--user", "show-environment"],
        env=launch_environment,
        capture_output=True,
        text=True,
    )
    if systemd_probe.returncode != 0:
        pytest.skip(f"user systemd manager is unavailable: {systemd_probe.stderr.strip()}")

    unit = f"proton-vpn-test-{uuid.uuid4().hex}.scope"
    marker_fd, marker_name = tempfile.mkstemp(prefix="proton-scope-")
    os.close(marker_fd)
    marker_file = Path(marker_name)
    marker_file.unlink()
    helper_code = (
        "import os, socket, sys, time; "
        "time.sleep(1); "
        "sock = socket.socket(); "
        "open(sys.argv[1], 'w', encoding='ascii').write(f'{os.getpid()} {sock.getsockopt(socket.SOL_SOCKET, socket.SO_MARK)}'); "
        "time.sleep(30)"
    )
    main_code = (
        "import os, subprocess, sys, time; "
        "helper = subprocess.Popen([sys.executable, '-c', sys.argv[2], sys.argv[1] + '.helper']); "
        "open(sys.argv[1], 'w', encoding='ascii').write(f'{os.getpid()} {helper.pid}'); "
        "time.sleep(30)"
    )
    launcher = ScopeLauncher(environ=launch_environment)
    manager = CgroupManager()
    launched_process = None
    selected_inode = None
    stopped = False
    cgroup_map = attached_bpf.get_table("cgroup_map")
    try:
        try:
            launch = launcher.launch(
                target_uid,
                unit,
                sys.executable,
                ["-c", main_code, str(marker_file), helper_code],
            )
            launched_process = launch
            identity = manager.wait_for_unit(target_uid, unit, timeout=3.0)
        except (OSError, CgroupDiscoveryUnavailable) as error:
            pytest.skip(f"managed user scope is unavailable: {error}")

        deadline = time.monotonic() + 3.0
        while not marker_file.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        helper_marker = Path(f"{marker_file}.helper")
        while not helper_marker.exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert marker_file.exists()
        assert helper_marker.exists()

        cgroup_map[ctypes.c_uint64(identity.inode)] = ctypes.c_uint32(1)
        selected_inode = identity.inode
        helper_pid = int(helper_marker.read_text(encoding="ascii").split()[0])
        helper_mark = int(helper_marker.read_text(encoding="ascii").split()[1])
        assert helper_mark == 245447468
        helper_cgroup = _cgroup_path_for_pid(helper_pid)
        assert helper_pid in {
            int(pid) for pid in (identity.path / "cgroup.procs").read_text().split()
        }
        assert helper_cgroup == identity.path
        subprocess.run(
            ["systemctl", "--user", "stop", unit],
            env=launch_environment,
            check=True,
            capture_output=True,
        )
        launched_process.wait(timeout=5)
        with pytest.raises(CgroupDiscoveryUnavailable):
            manager.identity_for_unit(target_uid, unit)
        del cgroup_map[ctypes.c_uint64(selected_inode)]
        stopped = True
    finally:
        if launched_process is not None and not stopped:
            subprocess.run(
                ["systemctl", "--user", "stop", unit],
                env=launch_environment,
                check=False,
                capture_output=True,
            )
        if selected_inode is not None and not stopped:
            try:
                del cgroup_map[ctypes.c_uint64(selected_inode)]
            except KeyError:
                pass
        marker_file.unlink(missing_ok=True)
        Path(f"{marker_file}.helper").unlink(missing_ok=True)

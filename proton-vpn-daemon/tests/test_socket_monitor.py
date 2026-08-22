import ctypes
from unittest.mock import MagicMock, Mock, call, patch

from proton.vpn.daemon.split_tunneling.apps.socket_monitor import BPF_PROGRAM, SocketMonitor


def test_socket_filter_looks_up_process_tgid():
    assert "bpf_get_current_cgroup_id()" in BPF_PROGRAM
    assert "BPF_HASH(cgroup_map, u64, u32)" in BPF_PROGRAM


@patch("proton.vpn.daemon.split_tunneling.apps.socket_monitor.os.open", return_value=123)
def test_start_attaches_valid_socket_create_hook(_mock_open):
    class AttachType:
        CGROUP_INET_SOCK_CREATE = 1
        CGROUP_INET4_CONNECT = 2
        CGROUP_INET6_CONNECT = 3

    bpf = Mock()
    bpf.CGROUP_SOCK = object()
    bpf.get_table.return_value = Mock()
    bpf.load_func.return_value = object()

    monitor = SocketMonitor.__new__(SocketMonitor)
    monitor._bpf = bpf
    monitor._bpf_cgroup_map = MagicMock()
    monitor._bpf_split_tunneling_func = object()
    monitor._cgroup = None
    monitor._attached_types = []
    monitor._bpf_enum_group = AttachType

    monitor.start()

    assert bpf.attach_func.call_count == 1
    assert bpf.attach_func.call_args == call(
        monitor._bpf_split_tunneling_func, 123, AttachType.CGROUP_INET_SOCK_CREATE
    )


def test_socket_filter_uses_cgroup_policy_map_only():
    assert "BPF_HASH(pid_map" not in BPF_PROGRAM
    assert "bpf_get_current_pid_tgid" not in BPF_PROGRAM


def test_socket_filter_marks_all_selected_cgroup_sockets():
    assert "if (cgroup_found)" in BPF_PROGRAM
    assert "dst_port" not in BPF_PROGRAM


def test_select_and_unselect_cgroup_update_map():
    monitor = SocketMonitor.__new__(SocketMonitor)
    monitor._cgroup = 123
    monitor._bpf_cgroup_map = MagicMock()
    monitor._bpf_cgroup_map.__contains__.return_value = True

    monitor.select_cgroup(15219)
    monitor.unselect_cgroup(15219)

    key = ctypes.c_uint64(15219)
    assert monitor._bpf_cgroup_map.__setitem__.call_args.args[0].value == key.value
    assert monitor._bpf_cgroup_map.__delitem__.call_args.args[0].value == key.value
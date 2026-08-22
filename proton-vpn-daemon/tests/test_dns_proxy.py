from unittest.mock import Mock

import pytest
from proton.vpn.daemon.split_tunneling.apps.cgroup_manager import DnsPolicy
from proton.vpn.daemon.split_tunneling.apps.dns_proxy import (
    DnsProxy,
    DnsProxyError,
    DnsUpstreams,
)


class FakeSocket:
    def __init__(self, response):
        self.response = response
        self.sent = None
        self.endpoint = None

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def settimeout(self, timeout):
        self.timeout = timeout

    def sendto(self, query, endpoint):
        self.sent = query
        self.endpoint = endpoint

    def recvfrom(self, _size):
        return self.response, (self.endpoint[0], self.endpoint[1])


class FailingSocket:
    def __enter__(self):
        raise OSError("timeout")

    def __exit__(self, *_args):
        return False


class FakeTcpSocket(FakeSocket):
    def __init__(self, response):
        super().__init__(response)
        self._stream = len(response).to_bytes(2, "big") + response
        self._offset = 0

    def connect(self, endpoint):
        self.endpoint = endpoint

    def sendall(self, payload):
        self.sent = payload

    def recv(self, size):
        if self._offset >= len(self._stream):
            return b""
        chunk = self._stream[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


def test_forwards_to_policy_selected_upstream():
    query = b"\x12\x34" + b"\x00" * 10 + b"query"
    response = b"\x12\x34" + b"\x80" * 10 + b"response"
    fake_socket = FakeSocket(response)
    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=lambda *args, **kwargs: fake_socket,
    )
    proxy.replace_policies({101: DnsPolicy.VPN, 202: DnsPolicy.PHYSICAL})

    assert proxy.forward_udp(query, 202) == response
    assert fake_socket.endpoint == ("192.168.1.1", 53)
    assert fake_socket.sent == query


def test_vpn_policy_selects_vpn_upstream():
    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53))
    )
    proxy.replace_policies({101: DnsPolicy.VPN})

    assert proxy.upstream_for(101) == ("10.2.0.1", 53)


def test_udp_failover_uses_second_endpoint():
    query = b"\x12\x34" + b"\x00" * 10
    response = b"\x12\x34" + b"\x80" * 10
    second = FakeSocket(response)
    attempts = iter([FailingSocket(), second])
    proxy = DnsProxy(
        DnsUpstreams(
            vpn=("10.2.0.1", 53),
            physical=("192.168.1.1", 53),
            physical_fallback=("9.9.9.9", 53),
        ),
        socket_factory=lambda *args, **kwargs: next(attempts),
    )
    proxy.replace_policies({202: DnsPolicy.PHYSICAL})

    assert proxy.forward_udp(query, 202) == response
    assert second.endpoint == ("9.9.9.9", 53)


def test_tcp_forwards_length_prefixed_query():
    query = b"\x12\x34" + b"\x00" * 10 + b"query"
    response = b"\x12\x34" + b"\x80" * 10 + b"response"
    fake_socket = FakeTcpSocket(response)
    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=lambda *args, **kwargs: fake_socket,
    )
    proxy.replace_policies({101: DnsPolicy.VPN})

    assert proxy.forward_tcp(query, 101) == response
    assert fake_socket.sent == len(query).to_bytes(2, "big") + query


@pytest.mark.parametrize("query", [b"", b"\x00" * 11])
def test_rejects_short_queries(query):
    proxy = DnsProxy(DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)))

    with pytest.raises(DnsProxyError):
        proxy.forward_udp(query, 101)


def test_rejects_unknown_cgroup_policy():
    proxy = DnsProxy(DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)))

    with pytest.raises(DnsProxyError):
        proxy.upstream_for(999)


def test_rejects_mismatched_response_transaction():
    query = b"\x12\x34" + b"\x00" * 10
    response = b"\x56\x78" + b"\x80" * 10
    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=Mock(return_value=FakeSocket(response)),
    )
    proxy.replace_policies({101: DnsPolicy.VPN})

    with pytest.raises(DnsProxyError):
        proxy.forward_udp(query, 101)


def test_blocks_encrypted_dns_for_physical_policy():
    proxy = DnsProxy(DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)))
    proxy.replace_policies({202: DnsPolicy.PHYSICAL})

    doh_payload = (
        b"GET /dns-query?name=example.com HTTP/1.1\r\n"
        b"Host: cloudflare-dns.com\r\n"
        b"Accept: application/dns-message\r\n\r\n"
    )

    with pytest.raises(DnsProxyError, match="Encrypted DNS"):
        proxy.validate_policy_for_flow(202, ("1.1.1.1", 443), doh_payload)


def test_allows_encrypted_dns_for_vpn_policy():
    proxy = DnsProxy(DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)))
    proxy.replace_policies({202: DnsPolicy.VPN})

    proxy.validate_policy_for_flow(202, ("1.1.1.1", 443), b"GET /dns-query HTTP/1.1")


def test_detects_dot_endpoints_as_encrypted_dns():
    assert DnsProxy.is_encrypted_dns_flow(("9.9.9.9", 853), None) is True
    assert DnsProxy.is_encrypted_dns_flow(("1.1.1.1", 443), b"GET /dns-query HTTP/1.1") is True
    assert DnsProxy.is_encrypted_dns_flow(("1.1.1.1", 80), b"GET / HTTP/1.1") is False


def test_vpn_and_physical_cgroups_use_different_upstreams():
    """Verify VPN cgroups and physical cgroups route to different DNS upstreams."""
    query = b"\x12\x34" + b"\x00" * 10 + b"example.com"
    vpn_response = b"\x12\x34" + b"\x81" + b"\x00" * 9 + b"vpn_answer"
    physical_response = b"\x12\x34" + b"\x81" + b"\x00" * 9 + b"phys_answer"

    responses = {
        ("10.2.0.1", 53): vpn_response,
        ("192.168.1.1", 53): physical_response,
    }

    def socket_factory(*args, **kwargs):
        fake_socket = FakeSocket(b"")
        original_sendto = fake_socket.sendto

        def sendto_and_respond(q, endpoint):
            original_sendto(q, endpoint)
            fake_socket.response = responses.get(endpoint, b"")

        fake_socket.sendto = sendto_and_respond
        return fake_socket

    proxy = DnsProxy(
        DnsUpstreams(
            vpn=("10.2.0.1", 53),
            vpn_fallback=None,
            physical=("192.168.1.1", 53),
            physical_fallback=None,
        ),
        socket_factory=socket_factory,
    )
    proxy.replace_policies({100: DnsPolicy.VPN, 200: DnsPolicy.PHYSICAL})

    assert proxy.forward_udp(query, 100) == vpn_response
    assert proxy.forward_udp(query, 200) == physical_response


def test_dns_provider_identity_no_cross_contamination():
    """Verify queries to one upstream never reach another (no provider mixing)."""
    query = b"\x12\x34" + b"\x00" * 10 + b"test"
    response = b"\x12\x34" + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"

    vpn_queries = []
    physical_queries = []

    def socket_factory(*args, **kwargs):
        fake_socket = FakeSocket(response)
        original_sendto = fake_socket.sendto

        def sendto_tracking(q, endpoint):
            original_sendto(q, endpoint)
            if endpoint == ("10.2.0.1", 53):
                vpn_queries.append(q)
            elif endpoint == ("192.168.1.1", 53):
                physical_queries.append(q)

        fake_socket.sendto = sendto_tracking
        return fake_socket

    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=socket_factory,
    )
    proxy.replace_policies({100: DnsPolicy.VPN, 200: DnsPolicy.PHYSICAL})

    proxy.forward_udp(query, 100)
    proxy.forward_udp(query, 200)

    assert len(vpn_queries) == 1
    assert len(physical_queries) == 1
    assert vpn_queries[0] == query
    assert physical_queries[0] == query


def test_policy_replacement_during_app_restart():
    """Verify DNS policies are atomically replaced when an app restarts with a new inode."""
    query = b"\x12\x34" + b"\x00" * 10
    old_response = b"\x12\x34" + b"\x81" + b"\x00" * 9 + b"old"
    new_response = b"\x12\x34" + b"\x81" + b"\x00" * 9 + b"new"

    responses = iter([old_response, new_response])

    def socket_factory(*args, **kwargs):
        return FakeSocket(next(responses))

    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=socket_factory,
    )

    proxy.replace_policies({1000: DnsPolicy.VPN})
    result1 = proxy.forward_udp(query, 1000)
    assert result1 == old_response

    proxy.replace_policies({1001: DnsPolicy.VPN})

    with pytest.raises(DnsProxyError):
        proxy.forward_udp(query, 1000)

    result2 = proxy.forward_udp(query, 1001)
    assert result2 == new_response


def test_multiple_cgroups_retain_independent_policies():
    """Verify multiple simultaneous cgroups each retain their own DNS policy."""
    query = b"\x12\x34" + b"\x00" * 10
    response = b"\x12\x34" + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"

    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=lambda *args, **kwargs: FakeSocket(response),
    )

    proxy.replace_policies(
        {1001: DnsPolicy.VPN, 1002: DnsPolicy.PHYSICAL, 1003: DnsPolicy.VPN}
    )

    assert proxy.upstream_for(1001) == ("10.2.0.1", 53)
    assert proxy.upstream_for(1002) == ("192.168.1.1", 53)
    assert proxy.upstream_for(1003) == ("10.2.0.1", 53)

    proxy.replace_policies(
        {1001: DnsPolicy.PHYSICAL, 1002: DnsPolicy.VPN, 1003: DnsPolicy.PHYSICAL}
    )

    assert proxy.upstream_for(1001) == ("192.168.1.1", 53)
    assert proxy.upstream_for(1002) == ("10.2.0.1", 53)
    assert proxy.upstream_for(1003) == ("192.168.1.1", 53)


def test_flatpak_scope_dns_policy_enforcement():
    """Verify Flatpak scopes are treated the same as native scopes for DNS policy."""
    query = b"\x12\x34" + b"\x00" * 10 + b"flatpak-test"
    response = b"\x12\x34" + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"

    fake_socket = FakeSocket(response)
    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=lambda *args, **kwargs: fake_socket,
    )

    flatpak_inode = 5000

    proxy.replace_policies({flatpak_inode: DnsPolicy.VPN})
    result = proxy.forward_udp(query, flatpak_inode)

    assert result == response
    assert fake_socket.endpoint == ("10.2.0.1", 53)


def test_snap_scope_dns_policy_enforcement():
    """Verify Snap scopes are treated the same as native scopes for DNS policy."""
    query = b"\x12\x34" + b"\x00" * 10 + b"snap-test"
    response = b"\x12\x34" + b"\x81\x80\x00\x01\x00\x01\x00\x00\x00\x00"

    fake_socket = FakeSocket(response)
    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=lambda *args, **kwargs: fake_socket,
    )

    snap_inode = 6000

    proxy.replace_policies({snap_inode: DnsPolicy.PHYSICAL})
    result = proxy.forward_udp(query, snap_inode)

    assert result == response
    assert fake_socket.endpoint == ("192.168.1.1", 53)


def test_tcp_dns_policy_routing_per_cgroup():
    """Verify TCP DNS queries are routed to the correct upstream per cgroup policy."""
    query = b"\x12\x34" + b"\x00" * 10 + b"tcp-test"
    vpn_response = b"\x12\x34" + b"\x81" + b"\x00" * 9 + b"vpn_tcp"
    physical_response = b"\x12\x34" + b"\x81" + b"\x00" * 9 + b"phy_tcp"

    endpoints_used = []

    class TrackingTcpSocket(FakeTcpSocket):
        def __init__(self, response):
            super().__init__(response)
            self._endpoint_used = None

        def connect(self, endpoint):
            self._endpoint_used = endpoint
            endpoints_used.append(endpoint)
            super().connect(endpoint)

    def socket_factory(*args, **kwargs):
        socket_obj = TrackingTcpSocket(b"")
        return socket_obj

    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=socket_factory,
    )
    proxy.replace_policies({100: DnsPolicy.VPN, 200: DnsPolicy.PHYSICAL})

    endpoints_used.clear()
    vpn_socket = FakeTcpSocket(vpn_response)

    def vpn_socket_factory(*args, **kwargs):
        return vpn_socket

    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=vpn_socket_factory,
    )
    proxy.replace_policies({100: DnsPolicy.VPN})
    vpn_result = proxy.forward_tcp(query, 100)
    assert vpn_result == vpn_response
    assert vpn_socket.endpoint == ("10.2.0.1", 53)

    physical_socket = FakeTcpSocket(physical_response)

    def physical_socket_factory(*args, **kwargs):
        return physical_socket

    proxy = DnsProxy(
        DnsUpstreams(vpn=("10.2.0.1", 53), physical=("192.168.1.1", 53)),
        socket_factory=physical_socket_factory,
    )
    proxy.replace_policies({200: DnsPolicy.PHYSICAL})
    physical_result = proxy.forward_tcp(query, 200)
    assert physical_result == physical_response
    assert physical_socket.endpoint == ("192.168.1.1", 53)

"""Cgroup-aware DNS forwarding boundary."""

from __future__ import annotations

import socket
from dataclasses import dataclass
from typing import Callable, Mapping

from proton.vpn.daemon.split_tunneling.apps.cgroup_manager import DnsPolicy


class DnsProxyError(RuntimeError):
    """A DNS query cannot be forwarded safely."""


@dataclass(frozen=True)
class DnsUpstreams:
    """Resolver endpoints for the two DNS policies, in failover order."""

    vpn: tuple[str, int]
    physical: tuple[str, int]
    vpn_fallback: tuple[str, int] | None = None
    physical_fallback: tuple[str, int] | None = None


class DnsProxy:
    """Forward raw DNS packets according to the cgroup policy."""

    def __init__(
        self,
        upstreams: DnsUpstreams,
        socket_factory: Callable[..., socket.socket] = socket.socket,
        timeout: float = 2.0,
    ):
        self._upstreams = upstreams
        self._socket_factory = socket_factory
        self._timeout = timeout
        self._policies: dict[int, DnsPolicy] = {}

    @staticmethod
    def is_encrypted_dns_flow(
        endpoint: tuple[str, int] | None, payload: bytes | None
    ) -> bool:
        """Return True for known encrypted DNS transports such as DoT/DoH."""
        if endpoint is None:
            return False
        _, port = endpoint
        if port == 853:
            return True
        if port != 443:
            return False
        if payload is None:
            return True
        normalized = payload.lower()
        return any(
            marker in normalized
            for marker in (
                b"dns-query",
                b"application/dns-message",
                b"application/dns",
                b"/dns-query",
                b"dot",
            )
        )

    def validate_policy_for_flow(
        self,
        cgroup_inode: int,
        endpoint: tuple[str, int] | None,
        payload: bytes | None = None,
    ) -> None:
        """Reject encrypted-DNS traffic if the cgroup policy says it must stay on the physical path."""
        if not self.is_encrypted_dns_flow(endpoint, payload):
            return
        policy = self._policies.get(cgroup_inode)
        if policy is None:
            raise DnsProxyError(f"No DNS policy is defined for cgroup inode {cgroup_inode}")
        if policy is DnsPolicy.PHYSICAL:
            raise DnsProxyError(
                f"Encrypted DNS flow is blocked for cgroup inode {cgroup_inode}: "
                "physical policy must not be bypassed"
            )

    def replace_policies(self, policies: Mapping[int, DnsPolicy]):
        """Atomically replace cgroup DNS policy used by subsequent queries."""
        self._policies = dict(policies)

    def upstream_for(self, cgroup_inode: int) -> tuple[str, int]:
        """Return the resolver endpoint selected for a cgroup inode."""
        try:
            policy = self._policies[cgroup_inode]
        except KeyError as error:
            raise DnsProxyError(
                f"No DNS policy is defined for cgroup inode {cgroup_inode}"
            ) from error
        return (
            self._upstreams.vpn
            if policy is DnsPolicy.VPN
            else self._upstreams.physical
        )

    def upstreams_for(self, cgroup_inode: int) -> tuple[tuple[str, int], ...]:
        """Return the configured resolver endpoints in failover order."""
        policy = self._policies.get(cgroup_inode)
        if policy is None:
            raise DnsProxyError(
                f"No DNS policy is defined for cgroup inode {cgroup_inode}"
            )
        primary, fallback = (
            (self._upstreams.vpn, self._upstreams.vpn_fallback)
            if policy is DnsPolicy.VPN
            else (self._upstreams.physical, self._upstreams.physical_fallback)
        )
        return (primary,) if fallback is None else (primary, fallback)

    def forward_udp(self, query: bytes, cgroup_inode: int) -> bytes:
        """Forward one DNS query and return a matching raw DNS response."""
        if len(query) < 12:
            raise DnsProxyError("DNS query is shorter than its header")
        policy = self._policies.get(cgroup_inode)
        if policy is None:
            raise DnsProxyError(f"No DNS policy is defined for cgroup inode {cgroup_inode}")
        if policy is DnsPolicy.PHYSICAL:
            self.validate_policy_for_flow(cgroup_inode, self._upstreams.physical, query)
        last_error = None
        for upstream in self.upstreams_for(cgroup_inode):
            try:
                with self._socket_factory(socket.AF_INET, socket.SOCK_DGRAM) as dns_socket:
                    dns_socket.settimeout(self._timeout)
                    dns_socket.sendto(query, upstream)
                    response, _ = dns_socket.recvfrom(65535)
                self._validate_response(query, response)
                return response
            except (DnsProxyError, OSError) as error:
                last_error = error
        raise DnsProxyError(f"DNS upstream request failed: {last_error}") from last_error

    def forward_tcp(self, query: bytes, cgroup_inode: int) -> bytes:
        """Forward one DNS-over-TCP query using two-byte length framing."""
        if len(query) < 12:
            raise DnsProxyError("DNS query is shorter than its header")
        policy = self._policies.get(cgroup_inode)
        if policy is None:
            raise DnsProxyError(f"No DNS policy is defined for cgroup inode {cgroup_inode}")
        if policy is DnsPolicy.PHYSICAL:
            self.validate_policy_for_flow(cgroup_inode, self._upstreams.physical, query)
        last_error = None
        for upstream in self.upstreams_for(cgroup_inode):
            try:
                with self._socket_factory(socket.AF_INET, socket.SOCK_STREAM) as dns_socket:
                    dns_socket.settimeout(self._timeout)
                    dns_socket.connect(upstream)
                    dns_socket.sendall(len(query).to_bytes(2, "big") + query)
                    response_length = int.from_bytes(self._recv_exact(dns_socket, 2), "big")
                    response = self._recv_exact(dns_socket, response_length)
                self._validate_response(query, response)
                return response
            except (DnsProxyError, OSError) as error:
                last_error = error
        raise DnsProxyError(f"DNS upstream request failed: {last_error}") from last_error

    @staticmethod
    def _recv_exact(dns_socket, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = dns_socket.recv(size - len(chunks))
            if not chunk:
                raise DnsProxyError("DNS upstream closed the connection")
            chunks.extend(chunk)
        return bytes(chunks)

    @staticmethod
    def _validate_response(query: bytes, response: bytes):
        if len(response) < 12 or response[:2] != query[:2]:
            raise DnsProxyError("DNS response does not match the query transaction")

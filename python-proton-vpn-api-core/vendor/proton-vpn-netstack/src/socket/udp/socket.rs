use core::net;
use std::net::SocketAddr;

use smoltcp::phy::PacketMeta;
use smoltcp::socket::udp::{self, UdpMetadata};
use smoltcp::wire::IpEndpoint;
use tracing::{debug, trace};

use crate::Udp;
use crate::error::{ReadError, ReadResult, WriteError, WriteResult};
use crate::socket::option::common::HopLimit;
use crate::socket::option::private::GetSockOptImpl;
use crate::socket::option::{GetSockOpt, SetSockOpt};
use crate::socket::{RawSocketHandle, SocketHandle};

#[derive(Debug)]
pub struct UdpSocket {
    handle: SocketHandle<Udp>,
    local_addr: net::SocketAddr,
}

impl UdpSocket {
    /// "connect" to the remote address.
    ///
    /// All datagrams from different addresses will be dropped
    pub fn connect(self, addr: impl Into<net::SocketAddr>) -> ConnectedUdpSocket {
        ConnectedUdpSocket {
            socket: self,
            endpoint: addr.into(),
        }
    }

    /// Get the address the socket in bound locally
    pub fn local_addr(&self) -> net::SocketAddr {
        self.local_addr
    }

    /// Receive a datagram from a remote address and place it in `buf`.
    ///
    /// On success, returns the number of bytes read (the size of the datagram)
    /// and the address from which we received this datagram.
    ///
    /// # Errors
    /// If `buf` is not big enough to fit the datagram returns
    /// [ReadError::InvalidInput].
    ///
    /// If there is no datagram to read, returns [ReadError::WouldBlock]
    pub fn sync_recv_from(&self, buf: &mut [u8]) -> ReadResult<(usize, net::SocketAddr)> {
        self.sync_readmsg(buf, false)
    }

    /// Receive a datagram from a remote address and place it in `buf`, but do
    /// not remove it from the internal buffer. Subsequent call to this function
    /// returns the same result unlike [`Self::sync_recv_from`].
    ///
    /// On success, returns the number of bytes read (the size of the datagram)
    /// and the address from which we received this datagram.
    ///
    /// # Errors
    /// If `buf` is not big enough to fit the datagram returns
    /// [ReadError::InvalidInput].
    ///
    /// If there is no datagram to read, returns [ReadError::WouldBlock]
    pub fn sync_peek_from(&self, buf: &mut [u8]) -> ReadResult<(usize, net::SocketAddr)> {
        self.sync_readmsg(buf, true)
    }

    #[cfg(feature = "async")]
    /// Async variant of [Self::sync_recv_from]
    pub async fn recv_from(&self, buf: &mut [u8]) -> ReadResult<(usize, net::SocketAddr)> {
        std::future::poll_fn(|cx| self.poll_readmsg(cx, buf, false)).await
    }

    #[cfg(feature = "async")]
    /// Async variant of [Self::sync_peek_from]
    pub async fn peek_from(&self, buf: &mut [u8]) -> ReadResult<(usize, net::SocketAddr)> {
        std::future::poll_fn(|cx| self.poll_readmsg(cx, buf, true)).await
    }

    #[cfg(feature = "async")]
    pub fn poll_sendto(
        &self,
        cx: &mut core::task::Context<'_>,
        buf: &[u8],
        endpoint: IpEndpoint,
    ) -> core::task::Poll<WriteResult<()>> {
        self.handle
            .socket_iface
            .with_udp_socket_mut(
                self.handle.handle,
                |socket| match write_to_socket_interface(
                    buf,
                    socket,
                    self.meta(endpoint),
                    &self.handle,
                ) {
                    Err(WriteError::WouldBlock) => {
                        socket.register_send_waker(cx.waker());
                        core::task::Poll::Pending
                    }
                    finished => core::task::Poll::Ready(finished),
                },
            )
    }

    #[cfg(feature = "async")]
    /// Async variant of [Self::sync_sendto]
    pub async fn sendto(&self, buf: &[u8], endpoint: IpEndpoint) -> WriteResult<()> {
        std::future::poll_fn(|cx| self.poll_sendto(cx, buf, endpoint)).await
    }

    /// Send the payload contained in `buf` to `endpoint`.
    pub fn sync_sendto(&self, buf: &[u8], endpoint: IpEndpoint) -> WriteResult<usize> {
        self.handle
            .socket_iface
            .with_udp_socket_mut(self.raw_handle(), |socket| {
                write_to_socket_interface(buf, socket, self.meta(endpoint), &self.handle)
            })
            .map(|_| buf.len())
    }

    /// See [Self::sync_recv], except that this function yield on
    /// [ReadError::WouldBlock]
    #[cfg(feature = "async")]
    fn poll_readmsg(
        &self,
        cx: &mut core::task::Context<'_>,
        buf: &mut [u8],
        msg_peek: bool,
    ) -> core::task::Poll<ReadResult<(usize, net::SocketAddr)>> {
        self.handle
            .socket_iface
            .with_udp_socket_mut(
                self.handle.handle,
                |socket| match read_from_socket_interface(buf, socket, &self.handle, msg_peek) {
                    Err(ReadError::WouldBlock) => {
                        socket.register_recv_waker(cx.waker());
                        std::task::Poll::Pending
                    }
                    finished => std::task::Poll::Ready(finished),
                },
            )
    }

    /// Read a datagram from this socket and place it into `buf`.
    /// To peek, use `msg_peek=true`.
    ///
    /// Returns the peer that is the origin of the datagram along with the
    /// datagram's size.
    fn sync_readmsg(&self, buf: &mut [u8], msg_peek: bool) -> ReadResult<(usize, net::SocketAddr)> {
        self.handle
            .socket_iface
            .with_udp_socket_mut(self.raw_handle(), |socket| {
                read_from_socket_interface(buf, socket, &self.handle, msg_peek)
            })
    }

    pub(crate) fn raw_handle(&self) -> RawSocketHandle {
        self.handle.handle
    }

    pub(crate) fn new(handle: SocketHandle<Udp>, local_addr: net::SocketAddr) -> Self {
        Self { handle, local_addr }
    }

    fn meta(&self, endpoint: IpEndpoint) -> UdpMetadata {
        UdpMetadata {
            endpoint,
            local_address: Some(self.local_addr.ip().into()),
            meta: PacketMeta::default(),
        }
    }
}

impl SetSockOpt<HopLimit> for UdpSocket {
    fn setsockopt(self, option: HopLimit) -> Self {
        tracing::info!("setsockopt: {option:?}");
        self.handle
            .socket_iface
            .with_udp_socket_mut(&self.handle, |s| s.set_hop_limit(Some(option.into())));
        self
    }
}

impl GetSockOptImpl<HopLimit> for UdpSocket {
    fn getsockopt(&self) -> HopLimit {
        self.handle
            .socket_iface
            .with_udp_socket_mut(&self.handle, |s| s.hop_limit())
            .map(|hop_limit| HopLimit::try_new(hop_limit).expect("smoltcp panics on hop_limit=0"))
            .unwrap_or_default()
    }
}

impl GetSockOpt for UdpSocket {}

#[derive(Debug)]
pub struct ConnectedUdpSocket {
    socket: UdpSocket,
    endpoint: net::SocketAddr,
}

impl<T: Default> SetSockOpt<T> for ConnectedUdpSocket
where
    UdpSocket: SetSockOpt<T>,
{
    fn setsockopt(self, option: T) -> Self {
        Self {
            socket: self.socket.setsockopt(option),
            endpoint: self.endpoint,
        }
    }
}

impl<T: Default> GetSockOptImpl<T> for ConnectedUdpSocket
where
    UdpSocket: GetSockOptImpl<T>,
{
    fn getsockopt(&self) -> T {
        GetSockOpt::getsockopt::<T>(&self.socket)
    }
}

impl GetSockOpt for ConnectedUdpSocket {}

impl ConnectedUdpSocket {
    /// See [UdpSocket::local_addr]
    pub fn local_addr(&self) -> net::SocketAddr {
        self.socket.local_addr
    }

    /// Receive a datagram from a remote address and place it in `buf`.
    ///
    /// On success, returns the number of bytes read (the size of the datagram)
    /// and the address from which we received this datagram.
    ///
    /// # Errors
    /// If `buf` is not big enough to fit the datagram returns
    /// [ReadError::InvalidInput].
    ///
    /// If there is no datagram to read, returns [ReadError::WouldBlock]
    pub fn sync_recv(&self, buf: &mut [u8]) -> ReadResult<usize> {
        loop_until_correct_endpoint!(self.endpoint, self.socket.sync_recv_from(buf))
    }

    /// See [UdpSocket::sync_peek_from] except that the datagram are discarded
    /// if they don't come from the connected destination
    pub fn sync_peek(&self, buf: &mut [u8]) -> ReadResult<usize> {
        loop_until_correct_endpoint!(self.endpoint, self.socket.sync_peek_from(buf))
    }

    #[cfg(feature = "async")]
    /// See [UdpSocket::recv_from] except that the datagram are discarded
    /// if they don't come from the connected destination
    pub async fn recv(&self, buf: &mut [u8]) -> ReadResult<usize> {
        loop_until_correct_endpoint!(self.endpoint, self.socket.recv_from(buf).await)
    }

    #[cfg(feature = "async")]
    /// See [UdpSocket::peek_from] except that the datagram are discarded
    /// if they don't come from the connected destination
    pub async fn peek(&self, buf: &mut [u8]) -> ReadResult<usize> {
        loop_until_correct_endpoint!(self.endpoint, self.socket.peek_from(buf).await)
    }

    #[cfg(feature = "async")]
    pub fn poll_send(
        &self,
        cx: &mut core::task::Context<'_>,
        buf: &[u8],
    ) -> core::task::Poll<WriteResult<()>> {
        self.socket.poll_sendto(cx, buf, self.endpoint.into())
    }

    #[cfg(feature = "async")]
    /// See [UdpSocket::sendto] except that the datagram are sent to the
    /// connected destination only
    pub async fn send(&self, buf: &[u8]) -> WriteResult<()> {
        self.socket.sendto(buf, self.endpoint.into()).await
    }

    /// See [UdpSocket::sync_sendto] except that the datagram are sent to the
    /// connected destination only
    pub fn sync_send(&self, buf: &[u8]) -> WriteResult<usize> {
        self.socket.sync_sendto(buf, self.endpoint.into())
    }
}

fn write_to_socket_interface<T>(
    buf: &[u8],
    socket: &mut udp::Socket,
    meta: UdpMetadata,
    waker: &SocketHandle<T>,
) -> WriteResult<()> {
    debug!("attempt write {} bytes ", buf.len());

    if socket.can_send() {
        socket.send_slice(buf, meta).map_err(|e| match e {
            udp::SendError::BufferFull => WriteError::NoBufferSpaceAvailable,
            udp::SendError::Unaddressable => WriteError::AddressNotAvailable,
        })?;
        waker.wake_read();
        return Ok(());
    }
    debug!("write would block");

    Err(WriteError::WouldBlock)
}

fn read_from_socket_interface<T>(
    buf: &mut [u8],
    socket: &mut udp::Socket<'_>,
    waker: &SocketHandle<T>,
    msg_peek: bool,
) -> ReadResult<(usize, net::SocketAddr)> {
    debug!("attempt read {} bytes MSG_PEEK={msg_peek}", buf.len());
    if socket.can_recv() {
        let read = if msg_peek {
            socket.peek_slice(buf).map(|(len, meta)| (len, *meta))
        } else {
            socket.recv_slice(buf)
        };

        match read {
            Ok((read, meta)) => {
                waker.wake_write();
                debug!("read {read} meta:{meta:?}");
                let peer_addr = net::SocketAddr::from((
                    net::IpAddr::from(meta.endpoint.addr),
                    meta.endpoint.port,
                ));
                return Ok((read, peer_addr));
            }
            Err(udp::RecvError::Exhausted) => return Err(ReadError::WouldBlock),
            Err(udp::RecvError::Truncated) => {
                trace!("packet truncated: invalid input");
                return Err(ReadError::InvalidInput);
            }
        }
    }

    debug!("read would block");

    Err(ReadError::WouldBlock)
}

fn filter_endpoint(
    endpoint: SocketAddr,
    (len, peer): (usize, SocketAddr),
) -> Option<(usize, SocketAddr)> {
    (peer == endpoint).then_some((len, peer))
}

macro_rules! loop_until_correct_endpoint {
    ($endpoint: expr, $func: expr) => {{
        loop {
            let res = $func?;
            if let Some((len, _)) = filter_endpoint($endpoint, res) {
                return Ok(len);
            }
        }
    }};
}
use loop_until_correct_endpoint;

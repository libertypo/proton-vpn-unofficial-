use core::cell::Cell;
use core::net;

use derive_more::Debug;
use smoltcp::socket::tcp;
use tracing::{debug, info};

use crate::Tcp;
use crate::error::{ConnectError, ConnectResult, ReadError, ReadResult, WriteError, WriteResult};
use crate::socket::SocketHandle;
use crate::socket::option::common::HopLimit;
use crate::socket::option::private::GetSockOptImpl;
use crate::socket::option::{GetSockOpt, SetSockOpt};
use crate::socket::tcp::option::{
    AckDelay, CongestionControl, KeepAlive, Nagle, Timeout, Timestamp,
};

#[derive(Debug)]
pub struct TcpStream {
    pub(crate) handle: SocketHandle<Tcp>,
    local_addr: net::SocketAddr,
    peer_addr: Cell<Option<net::SocketAddr>>,
}

impl SetSockOpt<HopLimit> for TcpStream {
    fn setsockopt(self, hop_limit: HopLimit) -> Self {
        tracing::info!("setsockopt: {hop_limit:?}");
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.set_hop_limit(Some(hop_limit.into())));
        self
    }
}

impl GetSockOptImpl<HopLimit> for TcpStream {
    fn getsockopt(&self) -> HopLimit {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.hop_limit())
            .map(|hop_limit| HopLimit::try_new(hop_limit).expect("smoltcp panics on hop_limit=0"))
            .unwrap_or_default()
    }
}

impl SetSockOpt<CongestionControl> for TcpStream {
    fn setsockopt(self, control: CongestionControl) -> Self {
        tracing::info!("setsockopt: {control:?}");
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.set_congestion_control(control.into()));
        self
    }
}

impl GetSockOptImpl<CongestionControl> for TcpStream {
    fn getsockopt(&self) -> CongestionControl {
        let ctrl = self
            .handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.congestion_control());
        CongestionControl::new(ctrl)
    }
}

impl SetSockOpt<Nagle> for TcpStream {
    fn setsockopt(self, nagle: Nagle) -> Self {
        tracing::info!("setsockopt: {nagle:?}");
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.set_nagle_enabled(nagle.into()));
        self
    }
}

impl GetSockOptImpl<Nagle> for TcpStream {
    fn getsockopt(&self) -> Nagle {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.nagle_enabled())
            .into()
    }
}

impl SetSockOpt<Timeout> for TcpStream {
    fn setsockopt(self, timeout: Timeout) -> Self {
        tracing::info!("setsockopt: {timeout:?}");
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.set_timeout(timeout.into()));
        self
    }
}

impl GetSockOptImpl<Timeout> for TcpStream {
    fn getsockopt(&self) -> Timeout {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.timeout())
            .into()
    }
}

impl SetSockOpt<KeepAlive> for TcpStream {
    fn setsockopt(self, keep_alive: KeepAlive) -> Self {
        tracing::info!("setsockopt: {keep_alive:?}");
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.set_keep_alive(keep_alive.into()));
        self
    }
}

impl GetSockOptImpl<KeepAlive> for TcpStream {
    fn getsockopt(&self) -> KeepAlive {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.keep_alive())
            .into()
    }
}

impl SetSockOpt<AckDelay> for TcpStream {
    fn setsockopt(self, ack_delay: AckDelay) -> Self {
        tracing::info!("setsockopt: {ack_delay:?}");
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.set_ack_delay(ack_delay.into()));
        self
    }
}

impl GetSockOptImpl<AckDelay> for TcpStream {
    fn getsockopt(&self) -> AckDelay {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.ack_delay())
            .into()
    }
}

/// Write only: [smoltcp] never hands the generator back, so there is no
/// [GetSockOpt] counterpart.
impl SetSockOpt<Timestamp> for TcpStream {
    fn setsockopt(self, timestamp: Timestamp) -> Self {
        tracing::info!("setsockopt: {timestamp:?}");
        self.handle
            .socket_iface
            .with_tcp_socket_mut(&self.handle, |s| s.set_tsval_generator(timestamp.into()));
        self
    }
}

impl GetSockOpt for TcpStream {}

impl TcpStream {
    /// Get the locally bound address
    pub fn local_addr(&self) -> net::SocketAddr {
        self.local_addr
    }

    /// Get the address of our peer.
    ///
    /// # Error
    /// [ErrorKind::NotConnected] if the socket is not _yet_ connected
    pub fn peer_addr(&self) -> Option<net::SocketAddr> {
        if let addr @ Some(_) = self.peer_addr.get() {
            return addr;
        }

        self.handle
            .socket_iface
            .with_tcp_socket_mut(self.handle.handle, |socket| {
                if Self::is_connected(socket)
                    && let Some(endpoint) = socket.remote_endpoint()
                {
                    self.peer_addr.set(Some(net::SocketAddr::new(
                        endpoint.addr.into(),
                        endpoint.port,
                    )));
                }
            });

        self.peer_addr.get()
    }

    /// Shutdown the socket in WRITE mode (SHUT_WR).
    ///
    /// This sends a TCP_FIN packet
    pub fn shutdown(&self) -> WriteResult<()> {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(self.handle.handle, |socket| {
                shutdown_socket(socket, &self.handle)
            })
    }

    pub fn read_bytes(&mut self, buf: &mut [u8]) -> ReadResult<usize> {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(self.handle.handle, |socket| {
                read_from_socket_interface(buf, socket, &self.handle)
            })
    }

    pub fn write_bytes(&mut self, buf: &[u8]) -> WriteResult<usize> {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(self.handle.handle, |socket| {
                write_to_socket_interface(buf, socket, &self.handle)
            })
    }

    #[cfg(feature = "async")]
    pub fn poll_read_bytes(
        self: core::pin::Pin<&mut Self>,
        cx: &mut core::task::Context<'_>,
        buf: &mut [u8],
    ) -> core::task::Poll<ReadResult<usize>> {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(
                self.handle.handle,
                |socket| match read_from_socket_interface(buf, socket, &self.handle) {
                    Err(ReadError::WouldBlock) => {
                        socket.register_recv_waker(cx.waker());
                        std::task::Poll::Pending
                    }
                    finished => std::task::Poll::Ready(finished),
                },
            )
    }

    #[cfg(feature = "async")]
    pub fn poll_write_bytes(
        self: core::pin::Pin<&mut Self>,
        cx: &mut core::task::Context<'_>,
        buf: &[u8],
    ) -> core::task::Poll<WriteResult<usize>> {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(
                self.handle.handle,
                |socket| match write_to_socket_interface(buf, socket, &self.handle) {
                    Err(WriteError::WouldBlock) => {
                        socket.register_send_waker(cx.waker());
                        core::task::Poll::Pending
                    }
                    finished => core::task::Poll::Ready(finished),
                },
            )
    }

    #[cfg(feature = "async")]
    pub fn poll_flush_stream(
        self: core::pin::Pin<&mut Self>,
        cx: &mut core::task::Context<'_>,
    ) -> core::task::Poll<WriteResult<()>> {
        self.handle.socket_iface.with_tcp_socket_mut(
            self.handle.handle,
            |socket| match flush_socket(socket) {
                Err(WriteError::WouldBlock) => {
                    socket.register_send_waker(cx.waker());
                    core::task::Poll::Pending
                }
                finished => core::task::Poll::Ready(finished),
            },
        )
    }

    #[cfg(feature = "async")]
    pub fn poll_close_stream(
        self: core::pin::Pin<&mut Self>,
        cx: &mut core::task::Context<'_>,
    ) -> core::task::Poll<WriteResult<()>> {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(self.handle.handle, |socket| {
                match shutdown_socket(socket, &self.handle) {
                    Err(WriteError::WouldBlock) => {
                        socket.register_send_waker(cx.waker());
                        core::task::Poll::Pending
                    }
                    finished => core::task::Poll::Ready(finished),
                }
            })
    }

    /// create a new TCP Stream (maybe not connected yet)
    pub(crate) fn new(
        handle: SocketHandle<Tcp>,
        local_addr: net::SocketAddr,
        peer_addr: Cell<Option<net::SocketAddr>>,
    ) -> Self {
        info!("connected to: {:?}", peer_addr);
        Self {
            handle,
            peer_addr,
            local_addr,
        }
    }

    fn is_connected(socket: &tcp::Socket) -> bool {
        socket.may_recv() && socket.may_send()
    }
}

#[derive(Debug, Default)]
pub enum AsyncTcpConnect {
    Error(ConnectError),
    Pending(TcpStream),

    #[default]
    Processing,
}

impl Future for AsyncTcpConnect {
    type Output = ConnectResult<TcpStream>;

    fn poll(
        self: core::pin::Pin<&mut Self>,
        cx: &mut core::task::Context<'_>,
    ) -> core::task::Poll<Self::Output> {
        let this = self.get_mut();
        let current_async_state = std::mem::replace(this, AsyncTcpConnect::Processing);

        match current_async_state {
            // if socket is in error, return it
            AsyncTcpConnect::Error(error) => std::task::Poll::Ready(Err(error)),

            AsyncTcpConnect::Pending(ref stream) => {
                // check state if we have to be awaken later on
                let state = stream.handle.socket_iface.with_tcp_socket_mut(
                    stream.handle.handle,
                    |socket| {
                        if socket.state() != smoltcp::socket::tcp::State::Closed
                            && socket.state() != smoltcp::socket::tcp::State::Established
                        {
                            socket.register_send_waker(cx.waker());
                        }
                        socket.state()
                    },
                );

                match state {
                    // if we are closed then it's ECONNRESET
                    tcp::State::Closed => {
                        std::task::Poll::Ready(Err(ConnectError::ConnectionReset))
                    }

                    // if we are established then we are good
                    tcp::State::Established => {
                        let AsyncTcpConnect::Pending(stream) = current_async_state else {
                            unreachable!()
                        };
                        std::task::Poll::Ready(Ok(stream))
                    }

                    // otherwise, come back later
                    _ => {
                        let _ = std::mem::replace(this, current_async_state);
                        std::task::Poll::Pending
                    }
                }
            }

            AsyncTcpConnect::Processing => panic!("already processing socket"),
        }
    }
}

#[cfg(all(feature = "async", feature = "std"))]
impl futures::AsyncRead for TcpStream {
    fn poll_read(
        self: core::pin::Pin<&mut Self>,
        cx: &mut core::task::Context<'_>,
        buf: &mut [u8],
    ) -> core::task::Poll<std::io::Result<usize>> {
        self.poll_read_bytes(cx, buf).map_err(Into::into)
    }
}

#[cfg(all(feature = "async", feature = "std"))]
impl futures::AsyncWrite for TcpStream {
    fn poll_write(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
        buf: &[u8],
    ) -> std::task::Poll<std::io::Result<usize>> {
        self.poll_write_bytes(cx, buf).map_err(Into::into)
    }

    fn poll_flush(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        self.poll_flush_stream(cx).map_err(Into::into)
    }

    fn poll_close(
        self: std::pin::Pin<&mut Self>,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<std::io::Result<()>> {
        self.poll_close_stream(cx).map_err(Into::into)
    }
}

#[cfg(feature = "std")]
impl std::io::Read for TcpStream {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        use std::io::ErrorKind;

        use ReadError;
        self.read_bytes(buf).map_err(|err| match err {
            ReadError::ConnectionReset => ErrorKind::ConnectionReset.into(),
            ReadError::WouldBlock => ErrorKind::WouldBlock.into(),
            ReadError::InvalidInput => ErrorKind::InvalidInput.into(),
        })
    }
}

#[cfg(feature = "std")]
impl std::io::Write for TcpStream {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        self.write_bytes(buf).map_err(Into::into)
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.handle
            .socket_iface
            .with_tcp_socket(self.handle.handle, flush_socket)
            .map_err(Into::into)
    }
}

#[allow(unused)]
fn flush_socket(socket: &tcp::Socket) -> WriteResult<()> {
    if !socket.may_send() {
        return Err(WriteError::BrokenPipe);
    }

    if socket.send_queue() == 0 {
        return Ok(());
    }

    Err(WriteError::WouldBlock)
}

fn write_to_socket_interface<T>(
    buf: &[u8],
    socket: &mut tcp::Socket,
    waker: &SocketHandle<T>,
) -> WriteResult<usize> {
    debug!("attempt write {} bytes ", buf.len());
    if !socket.may_send() {
        return Err(WriteError::BrokenPipe);
    }

    if socket.can_send() {
        let wrote = socket.send_slice(buf).map_err(|e| match e {
            tcp::SendError::InvalidState => WriteError::ConnectionReset,
        })?;
        waker.wake_read();
        return Ok(wrote);
    }
    debug!("write would block");

    Err(WriteError::WouldBlock)
}

fn read_from_socket_interface<T>(
    buf: &mut [u8],
    socket: &mut tcp::Socket<'_>,
    waker: &SocketHandle<T>,
) -> ReadResult<usize> {
    debug!("attempt read {} bytes ", buf.len());

    if !socket.may_recv() {
        debug!("socket closed");
        return Ok(0);
    }

    if socket.can_recv() {
        let read = socket.recv_slice(buf);
        match read {
            Ok(read) => {
                waker.wake_write();
                debug!("read {read}");

                return Ok(read);
            }
            Err(tcp::RecvError::Finished) => return Ok(0),
            Err(tcp::RecvError::InvalidState) => {
                return Err(ReadError::ConnectionReset);
            }
        }
    }

    debug!("read would block");

    Err(ReadError::WouldBlock)
}

fn shutdown_socket<T>(socket: &mut tcp::Socket<'_>, waker: &SocketHandle<T>) -> WriteResult<()> {
    if socket.may_send() {
        tracing::debug!("shutdown socket");
        socket.close();
        waker.wake_read();
    }

    if matches!(
        socket.state(),
        tcp::State::Closed | tcp::State::TimeWait | tcp::State::FinWait2
    ) {
        Ok(())
    } else {
        Err(WriteError::WouldBlock)
    }
}

use core::cell::Cell;
use std::marker::PhantomData;

use crate::error::{BindError, BindResult, ConnectError, ConnectResult, ListenError, ListenResult};
use crate::socket::tcp::TcpListener;
use crate::socket::{TcpStream, udp};
use crate::stack::SocketInterface;
use crate::{Tcp, TransportProtocol, Udp};
pub type RawSocketHandle = smoltcp::iface::SocketHandle;

#[derive(Debug)]
pub(crate) struct SocketHandle<Transport> {
    #[allow(unused)]
    pub(crate) handle: RawSocketHandle,
    pub(crate) maybe_local_addr: Option<core::net::SocketAddr>,
    pub(crate) socket_iface: SocketInterface,
    pub(crate) protocol: TransportProtocol,
    pub(crate) marker: PhantomData<Transport>,
}

impl<T> From<&SocketHandle<T>> for RawSocketHandle {
    fn from(val: &SocketHandle<T>) -> Self {
        val.handle
    }
}

impl<T: Into<TransportProtocol> + Default> SocketHandle<T> {
    pub(crate) fn new(handle: RawSocketHandle, stack: SocketInterface) -> Self {
        Self {
            handle,
            maybe_local_addr: None,
            socket_iface: stack,
            protocol: T::default().into(),
            marker: PhantomData,
        }
    }

    pub(crate) fn _bind(mut self, mut resource: core::net::SocketAddr) -> BindResult<Self> {
        let port = self
            .socket_iface
            .set_port::<T>(resource.port())
            .ok_or(BindError::AddressInUse)?;
        resource.set_port(port.get());
        let _ = self.maybe_local_addr.replace(resource);
        Ok(self)
    }
}

impl SocketHandle<Udp> {
    pub(crate) fn bind(mut self, endpoint: core::net::SocketAddr) -> BindResult<udp::UdpSocket> {
        self = self._bind(endpoint)?;
        let endpoint = self.maybe_local_addr.ok_or(BindError::NetworkUnreachable)?;
        self.socket_iface
            .with_udp_socket_mut(self.handle, |socket| socket.bind(endpoint))?;

        Ok(udp::UdpSocket::new(self, endpoint))
    }
}

impl SocketHandle<Tcp> {
    pub(crate) fn bind(self, endpoint: core::net::SocketAddr) -> BindResult<Self> {
        self._bind(endpoint)
    }

    pub(crate) fn listen(self, _: Option<u32>) -> ListenResult<TcpListener> {
        let addr = self
            .maybe_local_addr
            .ok_or(ListenError::NetworkUnreachable)?;

        {
            self.socket_iface
                .with_tcp_socket_mut(self.handle, |socket| socket.listen(addr))?;
        }

        Ok(TcpListener::new(self, addr))
    }

    pub(crate) fn connect(self, peer_addr: core::net::SocketAddr) -> ConnectResult<TcpStream> {
        let Some(ephemeral) = self.socket_iface.set_port::<Tcp>(0) else {
            return Err(ConnectError::AddressInUse);
        };

        let local_addr = self
            .socket_iface
            .connect(self.handle, ephemeral.get(), peer_addr)?;

        self.socket_iface.wake_read();

        Ok(TcpStream::new(self, local_addr, Cell::new(None)))
    }

    pub(super) fn bind_reuse(mut self, listener: &mut TcpListener) -> Self {
        let _ = self.maybe_local_addr.replace(listener.local_addr());
        self
    }
}

impl<T> SocketHandle<T> {
    pub fn wake_read(&self) {
        self.socket_iface.wake_read();
    }

    pub fn wake_write(&self) {
        self.socket_iface.wake_write();
    }
}

impl<T> Drop for SocketHandle<T> {
    fn drop(&mut self) {
        self.socket_iface.release_socket(
            self.handle,
            self.protocol,
            self.maybe_local_addr.map(|addr| {
                addr.port()
                    .try_into()
                    .expect("BUG: a socket can not have port 0 attributed as a local address")
            }),
        );
    }
}

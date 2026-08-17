use core::net;

use derive_more::Debug;
use smoltcp::socket::tcp;

use crate::Tcp;
use crate::error::{AcceptError, AcceptResult};
use crate::socket::RawSocketHandle;
use crate::socket::handle::SocketHandle;
use crate::socket::tcp::TcpStream;
use crate::utils::endpoint_into_socketaddr;

#[derive(Debug)]
pub struct TcpListener {
    handle: SocketHandle<Tcp>,
    local_addr: net::SocketAddr,
}

impl TcpListener {
    pub fn local_addr(&self) -> net::SocketAddr {
        self.local_addr
    }

    #[cfg(feature = "async")]
    pub fn poll_accept(
        &mut self,
        cx: &mut std::task::Context<'_>,
    ) -> std::task::Poll<AcceptResult<(TcpStream, net::SocketAddr)>> {
        futures::ready!(self.handle.socket_iface.with_tcp_socket_mut(
            self.handle.handle,
            |socket| {
                if socket.state() != tcp::State::Established {
                    socket.register_send_waker(cx.waker());
                    std::task::Poll::Pending
                } else {
                    std::task::Poll::Ready(())
                }
            }
        ));

        match self.accept_once() {
            Ok(stream) => {
                let peer = stream.peer_addr().expect("socket must be connected");
                std::task::Poll::Ready(Ok((stream, peer)))
            }
            Err(err) => std::task::Poll::Ready(Err(err)),
        }
    }

    #[cfg(feature = "async")]
    pub async fn accept(&mut self) -> AcceptResult<(TcpStream, net::SocketAddr)> {
        std::future::poll_fn(|cx| self.poll_accept(cx)).await
    }

    pub fn sync_accept(&mut self) -> AcceptResult<(TcpStream, net::SocketAddr)> {
        self.handle
            .socket_iface
            .with_tcp_socket_mut(self.handle.handle, |socket| {
                if socket.state() != tcp::State::Established {
                    Err(AcceptError::WouldBlock)
                } else {
                    Ok(())
                }
            })?;

        match self.accept_once() {
            Ok(stream) => {
                let peer = stream.peer_addr().expect("socket must be connected");
                Ok((stream, peer))
            }
            Err(err) => Err(err),
        }
    }
}

impl TcpListener {
    pub(crate) fn raw_handle(&self) -> RawSocketHandle {
        self.handle.handle
    }

    pub(crate) fn new(handle: SocketHandle<Tcp>, local_addr: net::SocketAddr) -> Self {
        Self { handle, local_addr }
    }

    fn accept_once(&mut self) -> AcceptResult<TcpStream> {
        let socket = self.handle.socket_iface.create_tcp_socket();

        let (peer_addr, local_addr) =
            self.handle
                .socket_iface
                .with_tcp_socket(self.raw_handle(), |socket| {
                    debug_assert_eq!(socket.state(), smoltcp::socket::tcp::State::Established);
                    (
                        endpoint_into_socketaddr(socket.remote_endpoint().unwrap()),
                        endpoint_into_socketaddr(socket.local_endpoint().unwrap()),
                    )
                });

        let new_listener = socket.bind_reuse(self).listen(None)?;

        let TcpListener { handle, .. } = std::mem::replace(self, new_listener);

        Ok(TcpStream::new(handle, local_addr, Some(peer_addr).into()))
    }
}

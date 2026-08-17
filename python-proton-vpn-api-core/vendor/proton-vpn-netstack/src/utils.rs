use core::net;

use smoltcp::wire::IpEndpoint;

pub(crate) fn endpoint_into_socketaddr(endpoint: IpEndpoint) -> net::SocketAddr {
    net::SocketAddr::new(endpoint.addr.into(), endpoint.port)
}

#[derive(Debug, Clone, Default)]
pub(crate) struct Wakers {
    maybe_read_waker: Option<std::task::Waker>,
    maybe_write_waker: Option<std::task::Waker>,
}

impl Wakers {
    pub fn read_as_mut(&mut self) -> &mut Option<std::task::Waker> {
        &mut self.maybe_read_waker
    }

    pub fn write_as_mut(&mut self) -> &mut Option<std::task::Waker> {
        &mut self.maybe_write_waker
    }

    pub fn read(&self) -> &Option<std::task::Waker> {
        &self.maybe_read_waker
    }

    pub fn write(&self) -> &Option<std::task::Waker> {
        &self.maybe_write_waker
    }
}

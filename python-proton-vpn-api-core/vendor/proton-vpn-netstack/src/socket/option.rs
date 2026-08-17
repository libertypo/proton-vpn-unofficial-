use crate::socket::option::private::GetSockOptImpl;

/// Common socket options
pub mod common;

/// A type that can set a socket option
pub trait SetSockOpt<SocketOption: Default> {
    fn setsockopt(self, option: SocketOption) -> Self;
}
/// A type that can get a socket option
pub trait GetSockOpt {
    #[must_use]
    fn getsockopt<SocketOption: Default>(&self) -> SocketOption
    where
        Self: GetSockOptImpl<SocketOption>,
    {
        GetSockOptImpl::getsockopt(self)
    }
}

pub(crate) mod private {
    use crate::socket::option::SetSockOpt;

    /// Implement this to get the socket option [super::GetSockOpt] is a syntactic sugar
    pub trait GetSockOptImpl<SocketOption: Default>: SetSockOpt<SocketOption> {
        #[must_use]
        fn getsockopt(&self) -> SocketOption;
    }
}

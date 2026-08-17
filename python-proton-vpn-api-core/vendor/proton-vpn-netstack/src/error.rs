use proton_os_interface::error::{IntoSystemError, SystemError};
use smoltcp::socket::{tcp, udp};

// re-export errors here
pub use crate::device::{DeviceReadError, DeviceWriteError};

#[derive(Debug, thiserror::Error)]
pub enum ReadError {
    #[error("connection reset by peer")]
    ConnectionReset,
    #[error("operation would have blocked")]
    WouldBlock,
    #[error("invalid input (e.g., buffer too small)")]
    InvalidInput,
}

impl IntoSystemError for ReadError {
    fn into_system_error(self) -> SystemError {
        match self {
            ReadError::ConnectionReset => SystemError::ConnectionReset,
            ReadError::WouldBlock => SystemError::WouldBlock,
            ReadError::InvalidInput => SystemError::InvalidInput,
        }
    }
}

#[cfg(feature = "std")]
impl From<ReadError> for std::io::Error {
    fn from(value: ReadError) -> Self {
        match value {
            ReadError::ConnectionReset => std::io::ErrorKind::ConnectionReset.into(),
            ReadError::WouldBlock => std::io::ErrorKind::WouldBlock.into(),
            ReadError::InvalidInput => std::io::ErrorKind::InvalidInput.into(),
        }
    }
}

pub type ReadResult<T> = Result<T, ReadError>;

#[derive(Debug, thiserror::Error)]
pub enum WriteError {
    #[error("broken pipe")]
    BrokenPipe,
    #[error("connection reset by peer")]
    ConnectionReset,
    #[error("operation would have blocked")]
    WouldBlock,
    #[error("No buffer space available")]
    NoBufferSpaceAvailable,
    #[error("Address not available")]
    AddressNotAvailable,
}

/// No buffer space available on POSIX
#[cfg(feature = "std")]
const ENOBUF: i32 = 105;

impl IntoSystemError for WriteError {
    fn into_system_error(self) -> SystemError {
        match self {
            WriteError::BrokenPipe => SystemError::NetworkUnreachable,
            WriteError::ConnectionReset => SystemError::ConnectionReset,
            WriteError::WouldBlock => SystemError::WouldBlock,
            // Best match I could find.
            WriteError::NoBufferSpaceAvailable => SystemError::QuotaExceeded,
            WriteError::AddressNotAvailable => SystemError::AddrNotAvailable,
        }
    }
}

#[cfg(feature = "std")]
impl From<WriteError> for std::io::Error {
    fn from(value: WriteError) -> Self {
        match value {
            WriteError::BrokenPipe => std::io::ErrorKind::BrokenPipe.into(),
            WriteError::ConnectionReset => std::io::ErrorKind::ConnectionReset.into(),
            WriteError::WouldBlock => std::io::ErrorKind::WouldBlock.into(),
            WriteError::NoBufferSpaceAvailable => std::io::Error::from_raw_os_error(ENOBUF),
            WriteError::AddressNotAvailable => std::io::ErrorKind::AddrNotAvailable.into(),
        }
    }
}

pub type WriteResult<T> = Result<T, WriteError>;

#[derive(Debug, thiserror::Error)]
pub enum ConnectError {
    #[error("network unreachable")]
    NetworkUnreachable,
    #[error("address already in use")]
    AddressInUse,
    #[error("connection reset by peer")]
    ConnectionReset,
}

impl From<smoltcp::socket::tcp::ConnectError> for ConnectError {
    fn from(value: smoltcp::socket::tcp::ConnectError) -> Self {
        match value {
            tcp::ConnectError::InvalidState => ConnectError::AddressInUse,
            tcp::ConnectError::Unaddressable => ConnectError::NetworkUnreachable,
        }
    }
}

impl IntoSystemError for ConnectError {
    fn into_system_error(self) -> SystemError {
        match self {
            ConnectError::NetworkUnreachable => SystemError::NetworkUnreachable,
            ConnectError::AddressInUse => SystemError::AddressInUse,
            ConnectError::ConnectionReset => SystemError::ConnectionReset,
        }
    }
}

pub type ConnectResult<T> = Result<T, ConnectError>;

#[derive(Debug, thiserror::Error)]
pub enum BindError {
    #[error("address already in use")]
    AddressInUse,
    #[error("network unreachable")]
    NetworkUnreachable,
}

impl IntoSystemError for BindError {
    fn into_system_error(self) -> SystemError {
        match self {
            BindError::NetworkUnreachable => SystemError::NetworkUnreachable,
            BindError::AddressInUse => SystemError::AddressInUse,
        }
    }
}

impl From<tcp::ListenError> for BindError {
    fn from(value: tcp::ListenError) -> Self {
        match value {
            tcp::ListenError::InvalidState => BindError::AddressInUse,
            tcp::ListenError::Unaddressable => BindError::NetworkUnreachable,
        }
    }
}

impl From<udp::BindError> for BindError {
    fn from(value: udp::BindError) -> Self {
        match value {
            udp::BindError::InvalidState => BindError::AddressInUse,
            udp::BindError::Unaddressable => BindError::NetworkUnreachable,
        }
    }
}

pub type BindResult<T> = Result<T, BindError>;

#[derive(Debug, thiserror::Error)]
pub enum AcceptError {
    #[error("network unreachable")]
    NetworkUnreachable,
    #[error("address already in use")]
    AddressInUse,
    #[error("operation would have blocked")]
    WouldBlock,
}

impl IntoSystemError for AcceptError {
    fn into_system_error(self) -> SystemError {
        match self {
            AcceptError::NetworkUnreachable => SystemError::NetworkUnreachable,
            AcceptError::AddressInUse => SystemError::AddressInUse,
            AcceptError::WouldBlock => SystemError::WouldBlock,
        }
    }
}

impl From<BindError> for AcceptError {
    fn from(value: BindError) -> Self {
        match value {
            BindError::NetworkUnreachable => Self::NetworkUnreachable,
            BindError::AddressInUse => Self::AddressInUse,
        }
    }
}

pub type AcceptResult<T> = Result<T, AcceptError>;

pub type ListenError = BindError;
pub type ListenResult<T> = Result<T, ListenError>;

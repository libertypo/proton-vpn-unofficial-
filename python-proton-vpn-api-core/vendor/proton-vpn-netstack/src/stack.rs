use core::net::SocketAddr;
use core::num::NonZeroU16;
use core::sync::atomic::AtomicBool;
use std::collections::HashMap;
use std::marker::PhantomData;
use std::sync::Arc;

use derive_more::Debug;
use proton_os_interface::io::TcpConnect;
use proton_os_interface::lock::{Mutex, MutexFactory};
use proton_os_interface::rand::{RngCore, SeedableRng};
use proton_os_interface::time::InstantFactory;
use rand_xoshiro::Xoroshiro128PlusPlus;
use smoltcp::iface::{Config, Interface, SocketSet};
use smoltcp::socket::{tcp, udp};
use smoltcp::storage::{PacketBuffer, PacketMetadata};
use smoltcp::time::Instant;
use smoltcp::wire::{HardwareAddress, IpListenEndpoint, Ipv4Cidr, Ipv6Cidr};
use tracing::trace;

use crate::device::{Device, DeviceReadError, DeviceWriteError};
#[cfg(feature = "async")]
use crate::error::ConnectError;
use crate::error::{BindResult, ConnectResult, ListenResult};
use crate::socket::{RawSocketHandle, SocketHandle, TcpListener, TcpStream, UdpSocket};
use crate::utils::Wakers;
use crate::{Deadline, NetworkConfig};

/// marker type indicating that the builder wants the current time
pub struct WantsTime;

/// marker type indicating that the builder wants the network config
pub struct WantsNetworkConfig;

/// marker type indicating that the builder wants a random source (non
/// cryptographically secure)
pub struct WantsRng;

/// marker type indicating that the builder is ready
pub struct Ready;

/// A builder of [`NetworkStack`]
#[derive(Debug)]
pub struct NetworkStackBuilder<T = WantsTime> {
    mtu: Option<u16>,
    rng: Option<Xoroshiro128PlusPlus>,
    network_config: Option<NetworkConfig>,
    now_as_millis: i64,
    marker: PhantomData<T>,
    socket_config: SocketBufferConfig,
}

impl NetworkStackBuilder {
    fn new() -> Self {
        Self {
            mtu: Default::default(),
            rng: Default::default(),
            network_config: Default::default(),
            now_as_millis: Default::default(),
            marker: Default::default(),
            socket_config: Default::default(),
        }
    }
}

impl<T> NetworkStackBuilder<T> {
    fn next<Next>(self) -> NetworkStackBuilder<Next> {
        NetworkStackBuilder {
            mtu: self.mtu,
            rng: self.rng,
            network_config: self.network_config,
            now_as_millis: self.now_as_millis,
            marker: PhantomData,
            socket_config: self.socket_config,
        }
    }
}

impl NetworkStackBuilder<WantsTime> {
    pub fn with_time(self, now_as_millis: i64) -> NetworkStackBuilder<WantsNetworkConfig> {
        let mut this = self.next();
        this.now_as_millis = now_as_millis;
        this
    }
}

impl NetworkStackBuilder<WantsNetworkConfig> {
    pub fn with_config(
        self,
        network_config: impl Into<NetworkConfig>,
    ) -> NetworkStackBuilder<WantsRng> {
        let mut this = self.next();
        this.network_config = Some(network_config.into());
        this
    }
}

impl NetworkStackBuilder<WantsRng> {
    pub fn with_rng(self, mut rng: impl RngCore) -> NetworkStackBuilder<Ready> {
        let mut this = self.next();
        this.rng = Some(Xoroshiro128PlusPlus::from_rng(&mut rng));
        this
    }
}

impl NetworkStackBuilder<Ready> {
    /// Set the MTU of the TUN interface
    pub fn with_mtu(mut self, mtu: u16) -> Self {
        self.mtu.replace(mtu);
        self
    }
    /// Set the TCP reception buffer size for *each* TCP sockets
    pub fn with_tcp_rx_buffer_size(mut self, buffer_size: usize) -> Self {
        self.socket_config.tcp_rx_buf_size = buffer_size;
        self
    }
    /// Set the TCP transmit buffer size for *each* TCP sockets
    pub fn with_tcp_tx_buffer_size(mut self, buffer_size: usize) -> Self {
        self.socket_config.tcp_tx_buf_size = buffer_size;
        self
    }
    /// Set the UDP reception buffer size for *each* UDP sockets
    pub fn with_udp_rx_buffer_size(mut self, buffer_size: usize) -> Self {
        self.socket_config.udp_rx_buf_size = buffer_size;
        self
    }
    /// Set the UDP transmit buffer size for *each* UDP sockets
    pub fn with_udp_tx_buffer_size(mut self, buffer_size: usize) -> Self {
        self.socket_config.udp_tx_buf_size = buffer_size;
        self
    }
    /// Set the maximum amount of UDP packet in each socket
    pub fn with_udp_rx_packet_capacity(mut self, max_packet_cap: usize) -> Self {
        self.socket_config.udp_rx_packet_capacity = max_packet_cap;
        self
    }
    /// Set the maximum amount of UDP packet in each socket
    pub fn with_udp_tx_packet_capacity(mut self, max_packet_cap: usize) -> Self {
        self.socket_config.udp_tx_packet_capacity = max_packet_cap;
        self
    }

    pub fn build<Mutex: MutexFactory>(self) -> NetworkStack {
        NetworkStack::from_builder::<Mutex>(self)
    }
}

/// A network stack providing on one side a factory to create L4 sockets and on
/// the other side a network interface (L3).
///
/// Writing to the sockets appears on the network interface.
/// Writing to the network interface appears on the sockets.
#[derive(Debug)]
pub struct NetworkStack {
    /// Where the individual sockets (TCP/UDP) are stored/managed
    sockets: SocketInterface,
    /// The configuration of the network stack
    config: NetworkConfig,
    /// The virtual physical device
    device: Device,
}

impl NetworkStack {
    pub fn builder() -> NetworkStackBuilder {
        NetworkStackBuilder::new()
    }

    #[deprecated = "use `builder() instead"]
    pub fn new_with_rng<Mutex: MutexFactory>(
        network_config: impl Into<NetworkConfig>,
        rng: impl RngCore + Send + 'static,
        now_as_millis: i64,
    ) -> Self {
        Self::builder()
            .with_time(now_as_millis)
            .with_config(network_config)
            .with_rng(rng)
            .build::<Mutex>()
    }

    fn from_builder<Mutex: MutexFactory>(
        NetworkStackBuilder {
            mtu,
            rng,
            network_config,
            now_as_millis,
            socket_config,
            ..
        }: NetworkStackBuilder<Ready>,
    ) -> Self {
        let network_config: NetworkConfig = network_config.expect("configuration should be there");
        let mut rng = rng.expect("RNG should be present");

        let mut device = mtu.map(Device::new).unwrap_or_default();

        let mut interface = Interface::new(
            Config::new(HardwareAddress::Ip),
            &mut device,
            Instant::from_millis(now_as_millis),
        );

        let (v4, v6) = network_config.clone().into_parts();

        if let Some(config) = v4 {
            interface.update_ip_addrs(|addrs| {
                addrs
                    .push(Ipv4Cidr::new(config.addr, config.prefix).into())
                    .unwrap()
            });
            if let Some(gateway) = config.gateway {
                interface
                    .routes_mut()
                    .add_default_ipv4_route(gateway)
                    .unwrap();
            }
        };

        if let Some(config) = v6 {
            interface.update_ip_addrs(|addrs| {
                addrs
                    .push(Ipv6Cidr::new(config.addr, config.prefix).into())
                    .unwrap()
            });
            if let Some(gateway) = config.gateway {
                interface
                    .routes_mut()
                    .add_default_ipv6_route(gateway)
                    .unwrap();
            }
        }

        Self {
            sockets: SocketInterface::new::<Mutex>(
                socket_config,
                interface,
                rand_xoshiro::Xoroshiro128PlusPlus::from_rng(&mut rng),
            ),
            config: network_config,
            device,
        }
    }

    /// Get the two sides of the network stack
    pub fn into_sides(self) -> (TransportFactory, NetworkInterface) {
        let NetworkStack {
            sockets,
            config,
            device,
        } = self;

        (
            TransportFactory {
                sockets: sockets.clone(),
                config,
            },
            NetworkInterface { sockets, device },
        )
    }
}

impl NetworkStack {
    /// Register an external waker for this network stack.
    ///
    /// This allows to be used with user-managed runtime (e.g., mio, epoll, ...)
    pub fn register_waker(&mut self, waker: impl std::task::Wake + Send + Sync + 'static) {
        let waker = Arc::new(waker);
        self.sockets
            .wakers
            .lock()
            .read_as_mut()
            .replace(std::task::Waker::from(waker.clone()));

        self.sockets
            .wakers
            .lock()
            .write_as_mut()
            .replace(std::task::Waker::from(waker));
    }
}

/// Factory to create L4 sockets
#[derive(Debug, Clone)]
pub struct TransportFactory {
    /// Where the individual sockets (TCP/UDP) are stored/managed
    sockets: SocketInterface,
    /// The configuration of the network stack
    config: NetworkConfig,
}

impl TransportFactory {
    /// Create a new listening socket bound on `addr`
    pub fn tcp_bind(&self, addr: impl Into<SocketAddr>) -> ListenResult<TcpListener> {
        let addr = self.set_address(addr.into());
        let socket = self.sockets.create_tcp_socket();
        let socket = socket.bind(addr)?;
        socket.listen(None)
    }

    #[cfg(feature = "async")]
    pub async fn async_tcp_connect(&self, dest: impl Into<SocketAddr>) -> ConnectResult<TcpStream> {
        let dest = dest.into();
        let socket = self.sockets.create_tcp_socket().connect(dest)?;
        crate::socket::AsyncTcpConnect::Pending(socket).await
    }

    pub fn tcp_connect(&self, dest: impl Into<SocketAddr>) -> ConnectResult<TcpStream> {
        self.sockets.create_tcp_socket().connect(dest.into())
    }

    pub fn udp_bind(&self, addr: impl Into<SocketAddr>) -> BindResult<UdpSocket> {
        let addr = self.set_address(addr.into());
        let socket = self.sockets.create_udp_socket();
        socket.bind(addr)
    }

    fn set_address(&self, mut addr: SocketAddr) -> SocketAddr {
        if addr.ip().is_unspecified() && addr.is_ipv6() {
            addr.set_ip(match &self.config {
                NetworkConfig::V6(network_config_v6)
                | NetworkConfig::Both(_, network_config_v6) => network_config_v6.addr.into(),
                _ => unreachable!("address must not be unspecified"),
            });
        }

        if addr.ip().is_unspecified() && addr.is_ipv4() {
            addr.set_ip(match &self.config {
                NetworkConfig::V4(network_config_v4)
                | NetworkConfig::Both(network_config_v4, _) => network_config_v4.addr.into(),
                _ => unreachable!("address must not be unspecified"),
            });
        }

        addr
    }
}

#[cfg(feature = "async")]
impl TcpConnect for TransportFactory {
    type Err = ConnectError;

    type Socket = TcpStream;

    async fn tcp_connect(&self, addr: core::net::SocketAddr) -> Result<Self::Socket, Self::Err> {
        self.async_tcp_connect(addr).await
    }
}

/// The transport protocol we support
#[non_exhaustive]
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) enum TransportProtocol {
    Tcp,
    Udp,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub(crate) struct Tcp;

impl From<Tcp> for TransportProtocol {
    fn from(_: Tcp) -> Self {
        TransportProtocol::Tcp
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub(crate) struct Udp;

impl From<Udp> for TransportProtocol {
    fn from(_: Udp) -> Self {
        TransportProtocol::Udp
    }
}

/// A network interface from which we can read ip/ipv6 packets coming from the
/// sockets
#[derive(Debug)]
pub struct NetworkInterface {
    /// Where the individual sockets (TCP/UDP) are stored/managed
    sockets: SocketInterface,
    /// The virtual physical device
    device: Device,
}

impl NetworkInterface {
    /// Transform this non-blocking interface into an async one
    pub fn into_async<T: InstantFactory>(
        self,
        time_provider: T,
    ) -> asynchronous::NetworkInterface<T> {
        asynchronous::NetworkInterface {
            interface: self,
            time_provider,
        }
    }

    pub fn read_packet(
        &mut self,
        buf: &mut [u8],
        timestamp_micros: impl Into<i64>,
    ) -> Result<usize, DeviceReadError> {
        {
            let mut sockets = self.sockets.inner.sockets.lock();
            let mut interface = self.sockets.inner.interface.lock();

            interface.poll(
                smoltcp::time::Instant::from_micros(timestamp_micros),
                &mut self.device,
                &mut sockets,
            )
        };

        self.device.read_packet(buf)
    }

    pub fn write_packet(
        &mut self,
        buf: &[u8],
        timestamp_micros: impl Into<i64>,
    ) -> Result<usize, DeviceWriteError> {
        let ret = self.device.write_packet(buf)?;

        {
            let mut sockets = self.sockets.inner.sockets.lock();
            let mut interface = self.sockets.inner.interface.lock();

            interface.poll(
                smoltcp::time::Instant::from_micros(timestamp_micros),
                &mut self.device,
                &mut sockets,
            )
        };

        Ok(ret)
    }
}

impl Deadline for NetworkInterface {
    fn next_deadline(&self, now: u64) -> Option<core::time::Duration> {
        let sockets = self.sockets.inner.sockets.lock();
        let mut interface = self.sockets.inner.interface.lock();

        interface
            .poll_delay(
                smoltcp::time::Instant::from_micros((now as i64) / 1_000),
                &sockets,
            )
            .map(|duration| std::time::Duration::from_micros(duration.total_micros()))
    }
}

#[cfg(feature = "std")]
impl std::io::Read for NetworkInterface {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        let timestamp = smoltcp::time::Instant::now();
        self.read_packet(buf, timestamp.total_micros())
            .map_err(|err| match err {
                DeviceReadError::WouldBlock => std::io::ErrorKind::WouldBlock.into(),
                DeviceReadError::NotEnoughSpace => {
                    std::io::Error::new(std::io::ErrorKind::InvalidInput, "not enough space")
                }
            })
    }
}

#[cfg(feature = "std")]
impl std::io::Write for NetworkInterface {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        let timestamp = smoltcp::time::Instant::now();
        self.write_packet(buf, timestamp.total_micros())
            .map_err(|err| match err {
                crate::device::DeviceWriteError::WouldBlock => {
                    std::io::ErrorKind::WouldBlock.into()
                }
            })
    }

    fn flush(&mut self) -> std::io::Result<()> {
        self.device
            .flush()
            .map_err(|_| std::io::ErrorKind::WouldBlock.into())
    }
}

pub mod asynchronous {
    use core::future::poll_fn;
    use core::time::Duration;

    use proton_os_interface::time::{Instant, InstantFactory, Since};

    use crate::device::{DeviceReadError, DeviceWriteError};

    pub struct NetworkInterface<TimeProvider> {
        pub(super) interface: super::NetworkInterface,
        pub(super) time_provider: TimeProvider,
    }

    impl<T: InstantFactory<Instant = Instant>> NetworkInterface<T> {
        pub fn poll_read(
            &mut self,
            buf: &mut [u8],
            context: &mut std::task::Context,
        ) -> std::task::Poll<Result<usize, DeviceReadError>> {
            let time = self
                .time_provider
                .now()
                .duration_since(Instant::from_duration(Duration::ZERO));

            let ret = self.interface.read_packet(buf, time.as_micros() as i64);
            if let Err(DeviceReadError::WouldBlock) = ret {
                self.interface
                    .sockets
                    .wakers
                    .lock()
                    .read_as_mut()
                    .replace(context.waker().clone());
                core::task::Poll::Pending
            } else {
                core::task::Poll::Ready(ret)
            }
        }

        pub async fn read(&mut self, buf: &mut [u8]) -> Result<usize, DeviceReadError> {
            poll_fn(|context| self.poll_read(buf, context)).await
        }

        pub fn poll_write(
            &mut self,
            buf: &[u8],
            context: &mut std::task::Context,
        ) -> std::task::Poll<usize> {
            let time = self
                .time_provider
                .now()
                .duration_since(Instant::from_duration(Duration::ZERO));

            match self.interface.write_packet(buf, time.as_micros() as i64) {
                Ok(written) => core::task::Poll::Ready(written),
                Err(DeviceWriteError::WouldBlock) => {
                    self.interface
                        .sockets
                        .wakers
                        .lock()
                        .write_as_mut()
                        .replace(context.waker().clone());
                    core::task::Poll::Pending
                }
            }
        }

        pub async fn write(&mut self, buf: &[u8]) -> usize {
            poll_fn(|context| self.poll_write(buf, context)).await
        }
    }
}

/// Configuration for socket buffers
#[derive(Debug, Clone)]
pub(crate) struct SocketBufferConfig {
    tcp_rx_buf_size: usize,
    tcp_tx_buf_size: usize,
    udp_rx_buf_size: usize,
    udp_tx_buf_size: usize,
    udp_tx_packet_capacity: usize,
    udp_rx_packet_capacity: usize,
}

impl SocketBufferConfig {
    const TCP_RX_CAP: usize = 1_048_576;
    const TCP_TX_CAP: usize = 1_048_576;
    const UDP_PACKET_CAP: usize = 65536;
    const UDP_RX_CAP: usize = 1_048_576;
    const UDP_TX_CAP: usize = 1_048_576;
}

impl Default for SocketBufferConfig {
    fn default() -> Self {
        Self {
            tcp_rx_buf_size: Self::TCP_RX_CAP,
            tcp_tx_buf_size: Self::TCP_TX_CAP,
            udp_rx_buf_size: Self::UDP_RX_CAP,
            udp_tx_buf_size: Self::UDP_TX_CAP,
            udp_tx_packet_capacity: Self::UDP_PACKET_CAP,
            udp_rx_packet_capacity: Self::UDP_PACKET_CAP,
        }
    }
}

#[derive(Debug, Clone)]
pub(crate) struct SocketInterface {
    socket_config: SocketBufferConfig,
    inner: Arc<SocketManagerInner>,
    #[debug(skip)]
    wakers: Arc<Mutex<Wakers>>,
}

impl SocketInterface {
    pub(crate) fn create_tcp_socket(&self) -> SocketHandle<Tcp> {
        let rx_buffer =
            smoltcp::socket::tcp::SocketBuffer::new(vec![0u8; self.socket_config.tcp_rx_buf_size]);
        let tx_buffer =
            smoltcp::socket::tcp::SocketBuffer::new(vec![0u8; self.socket_config.tcp_tx_buf_size]);
        let sock = tcp::Socket::new(rx_buffer, tx_buffer);

        let mut sockets = self.inner.sockets.lock();
        let handle = sockets.add(sock);

        SocketHandle::new(handle, self.clone())
    }

    pub(crate) fn create_udp_socket(&self) -> SocketHandle<Udp> {
        let rx_buffer = PacketBuffer::new(
            vec![PacketMetadata::EMPTY; self.socket_config.udp_rx_packet_capacity],
            vec![0u8; self.socket_config.udp_rx_buf_size],
        );

        let tx_buffer = PacketBuffer::new(
            vec![PacketMetadata::EMPTY; self.socket_config.udp_tx_packet_capacity],
            vec![0u8; self.socket_config.udp_tx_buf_size],
        );
        let sock = udp::Socket::new(rx_buffer, tx_buffer);

        let mut sockets = self.inner.sockets.lock();
        let handle = sockets.add(sock);

        SocketHandle::new(handle, self.clone())
    }

    pub(crate) fn connect(
        &self,
        raw_handle: impl Into<RawSocketHandle>,
        local_addr: impl Into<IpListenEndpoint>,
        peer_addr: core::net::SocketAddr,
    ) -> ConnectResult<core::net::SocketAddr> {
        let ret = self.with_tcp_socket_mut(raw_handle, |internal_socket| {
            internal_socket
                .connect(self.inner.interface.lock().context(), peer_addr, local_addr)
                .map(|_| internal_socket.local_endpoint().expect("we just connected"))
        })?;

        Ok(SocketAddr::from((ret.addr, ret.port)))
    }
}

impl SocketInterface {
    const SOCKET_TABLE_CAP: usize = 1024usize;

    pub(crate) fn new<Mutex>(
        socket_config: SocketBufferConfig,
        interface: Interface,
        mut rng: rand_xoshiro::Xoroshiro128PlusPlus,
    ) -> Self
    where
        Mutex: MutexFactory,
    {
        Self {
            inner: Arc::new(SocketManagerInner {
                interface: Mutex::new(interface),
                sockets: Mutex::new(SocketSet::new(Vec::with_capacity(Self::SOCKET_TABLE_CAP))),
                ports: [
                    (Tcp.into(), PortsManager::new()),
                    (Udp.into(), PortsManager::new()),
                ]
                .into(),
                rng: Mutex::new(Xoroshiro128PlusPlus::from_rng(&mut rng)),
            }),
            wakers: Arc::new(Mutex::new(Default::default())),
            socket_config,
        }
    }

    pub(crate) fn wake_read(&self) {
        if let Some(waker) = self.wakers.lock().read() {
            waker.wake_by_ref()
        }
    }

    pub(crate) fn wake_write(&self) {
        if let Some(waker) = self.wakers.lock().write() {
            waker.wake_by_ref()
        }
    }

    pub(crate) fn with_tcp_socket<T>(
        &self,
        handle: impl Into<RawSocketHandle>,
        callback: impl FnOnce(&tcp::Socket<'_>) -> T,
    ) -> T {
        self.with_socket_set(|socket_set| callback(socket_set.get::<tcp::Socket>(handle.into())))
    }

    pub(crate) fn with_tcp_socket_mut<T>(
        &self,
        handle: impl Into<RawSocketHandle>,
        callback: impl FnOnce(&mut tcp::Socket<'_>) -> T,
    ) -> T {
        self.with_socket_set_mut(|socket_set| {
            callback(socket_set.get_mut::<tcp::Socket>(handle.into()))
        })
    }

    pub(crate) fn with_udp_socket_mut<T>(
        &self,
        handle: impl Into<RawSocketHandle>,
        callback: impl FnOnce(&mut udp::Socket<'_>) -> T,
    ) -> T {
        self.with_socket_set_mut(|socket_set| {
            callback(socket_set.get_mut::<udp::Socket>(handle.into()))
        })
    }

    pub(crate) fn with_socket_set_mut<T>(
        &self,
        callback: impl FnOnce(&mut SocketSet<'static>) -> T,
    ) -> T {
        let mut locked_sock = self.inner.sockets.lock();
        callback(&mut locked_sock)
    }

    pub(crate) fn with_socket_set<T>(&self, callback: impl FnOnce(&SocketSet<'static>) -> T) -> T {
        let locked_sock = self.inner.sockets.lock();
        callback(&locked_sock)
    }

    pub(crate) fn release_socket(
        &mut self,
        handle: impl Into<RawSocketHandle>,
        protocol: TransportProtocol,
        port: Option<NonZeroU16>,
    ) {
        if let Some(port) = port {
            self.inner.ports[&protocol].release(port);
        }
        self.with_socket_set_mut(|socket_set| socket_set.remove(handle.into()));
    }

    pub(crate) fn set_port<Protocol: Into<TransportProtocol> + Default>(
        &self,
        port: u16,
    ) -> Option<NonZeroU16> {
        let protocol = Protocol::default().into();
        match port.try_into() {
            Ok(port) => self.inner.ports[&protocol].try_acquire(Acquire::port(port)),
            Err(_) => {
                let mut rng = self.inner.rng.lock();
                self.inner.ports[&protocol]
                    .try_acquire(Acquire::random(&mut rng as &mut dyn RngCore))
            }
        }
    }
}

#[derive(Debug)]
struct SocketManagerInner {
    #[debug(skip)]
    pub interface: Mutex<Interface>,
    pub ports: HashMap<TransportProtocol, PortsManager>,
    #[debug(skip)]
    rng: Mutex<Xoroshiro128PlusPlus>,
    pub sockets: Mutex<SocketSet<'static>>,
}

/// Strategy to use to acquire a port
pub struct Acquire<'a>(AcquireStrategy<'a>);

impl Acquire<'_> {
    pub fn port(port: NonZeroU16) -> Self {
        Self(AcquireStrategy::Port(port.into()))
    }
}

impl<'a> Acquire<'a> {
    /// use `rng` as a non-crypto safe rng.
    ///
    /// Note: we do not care about crypto safety here as we just randomize ports
    pub fn random(rng: &'a mut dyn RngCore) -> Self {
        Self(AcquireStrategy::Random(rng)) // nosemgrep
    }
}

enum AcquireStrategy<'a> {
    Random(&'a mut dyn RngCore), // nosemgrep
    Port(u16),
}

impl core::fmt::Debug for AcquireStrategy<'_> {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Random(_) => f.debug_tuple("Random").finish(), // nosemgrep
            Self::Port(arg0) => f.debug_tuple("Port").field(arg0).finish(),
        }
    }
}

#[derive(Debug)]
pub struct PortsManager {
    ephemeral_range: (u16, u16),
    ports_status: Vec<AtomicBool>,
}

impl Default for PortsManager {
    fn default() -> Self {
        Self::new()
    }
}

impl PortsManager {
    const DEFAULT_FIRST_EPHEMERAL: u16 = 49152;
    const LAST_PORT: u16 = u16::MAX;

    pub fn new() -> Self {
        Self::new_with_ephemeral_range(Self::DEFAULT_FIRST_EPHEMERAL, Self::LAST_PORT)
    }

    pub fn new_with_ephemeral_range(first_ephemeral: u16, last_ephemeral: u16) -> Self {
        let ports_status: Vec<_> = (0..Self::LAST_PORT)
            .map(|_| AtomicBool::new(false))
            .collect();
        Self {
            ports_status,
            ephemeral_range: (first_ephemeral, last_ephemeral),
        }
    }

    /// Trying to acquire port 0 or None will get the next free port
    pub fn try_acquire(&self, Acquire(acquire): Acquire) -> Option<NonZeroU16> {
        trace!("trying to acquire a port using {acquire:?}");
        match acquire {
            AcquireStrategy::Random(rng) => self.acquire_next(rng), // nosemgrep
            AcquireStrategy::Port(port) => {
                let status = self.ports_status.get(port2index(port))?;
                Self::acquire(port, status)
            }
        }
    }

    fn acquire(port: u16, status: &AtomicBool) -> Option<NonZeroU16> {
        status
            .compare_exchange(
                false,
                true,
                std::sync::atomic::Ordering::SeqCst,
                std::sync::atomic::Ordering::SeqCst,
            )
            .is_ok()
            .then_some(NonZeroU16::new(port)?)
    }

    fn acquire_next(&self, mut rng: impl RngCore) -> Option<NonZeroU16> {
        let from_offset = rng.next_u32() as u16;

        for i in 0..self.ephemeral_range_length() {
            let port = self.ephemeral_range.0
                + (from_offset.wrapping_add(i) % self.ephemeral_range_length());
            trace!("trying to acquire ephemeral {port} ");
            if let ret @ Some(_) = Self::acquire(port, self.ports_status.get(port2index(port))?) {
                return ret;
            }
        }

        None
    }

    pub fn release(&self, port: NonZeroU16) {
        let index = port2index(port);
        if let Some(status) = self.ports_status.get(index) {
            status.store(false, std::sync::atomic::Ordering::SeqCst);
        }
    }

    fn ephemeral_range_length(&self) -> u16 {
        let (first, last) = self.ephemeral_range;
        last.saturating_sub(first)
    }
}

fn port2index(port: impl Into<u16>) -> usize {
    port.into() as usize - 1
}
#[cfg(test)]
mod tests {
    use std::num::NonZeroU16;

    use crate::Acquire;
    use crate::stack::{PortsManager, port2index};

    #[test]
    fn port_to_index() {
        let port = NonZeroU16::new(1).unwrap();
        assert_eq!(port2index(port), 0);
    }

    #[test]
    fn acquire_port() {
        let ports = PortsManager::new();
        for i in ports.ephemeral_range.0..=ports.ephemeral_range.1 {
            let port = NonZeroU16::new(i).unwrap();
            assert_eq!(ports.try_acquire(Acquire::port(port)), Some(port));
        }
        let mut rng = rand::rng();
        assert_eq!(ports.try_acquire(Acquire::random(&mut rng)), None);
    }

    #[test]
    fn release_port() {
        let ports = PortsManager::new();
        let port = NonZeroU16::new(1).unwrap();
        assert_eq!(ports.try_acquire(Acquire::port(port)), Some(port));
        assert_eq!(ports.try_acquire(Acquire::port(port)), None);
        ports.release(port);
        assert_eq!(ports.try_acquire(Acquire::port(port)), Some(port));
    }
}

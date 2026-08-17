use core::time::Duration;

use derive_more::{From, Into};
use smoltcp::wire::TcpTimestampGenerator;
use tracing::warn;

/// The congestion control algorithm TCP is using.
#[derive(Debug, Into)]
pub struct CongestionControl(smoltcp::socket::tcp::CongestionControl);

/// By default uses [CongestionControl::reno()] if `tcp-cubic` feature is disabled.
/// Otherwise, uses <[CongestionControl::cubic()]>
impl Default for CongestionControl {
    fn default() -> Self {
        #[cfg(feature = "tcp-cubic")]
        {
            CongestionControl::cubic()
        }
        #[cfg(not(feature = "tcp-cubic"))]
        {
            CongestionControl::reno()
        }
    }
}

impl CongestionControl {
    /// Is it not using any congestion control?
    pub fn is_none(&self) -> bool {
        matches!(self.0, smoltcp::socket::tcp::CongestionControl::None)
    }

    /// uses https://intronetworks.cs.luc.edu/current/uhtml/reno.html#tcp-reno-and-congestion-management
    pub fn reno() -> Self {
        Self(smoltcp::socket::tcp::CongestionControl::Reno)
    }
    /// Does it use Reno congestion control algorithm?
    pub fn is_reno(&self) -> bool {
        matches!(self.0, smoltcp::socket::tcp::CongestionControl::Reno)
    }

    /// uses https://en.wikipedia.org/wiki/CUBIC_TCP
    #[cfg(feature = "tcp-cubic")]
    pub fn cubic() -> Self {
        Self(smoltcp::socket::tcp::CongestionControl::Cubic)
    }
    /// Does it use Cubic tcp?
    #[cfg(feature = "tcp-cubic")]
    pub fn is_cubic(&self) -> bool {
        matches!(self.0, smoltcp::socket::tcp::CongestionControl::Cubic)
    }

    /// Create a new [CongestionControl] but emits a warning if it uses
    /// [smoltcp::socket::tcp::CongestionControl::None]
    pub(crate) fn new(ctrl: smoltcp::socket::tcp::CongestionControl) -> Self {
        if matches!(ctrl, smoltcp::socket::tcp::CongestionControl::None) {
            warn!("no congestion control mechanism is in use");
        }
        Self(ctrl)
    }
}

/// Nagle's Algorithm, also known as "tinygram prevention".
///
/// When enabled, a segment smaller than the MSS is not sent while there is data in flight,
/// so at most one such segment is in flight at a time. This trades latency for a better
/// network utilization. Disabling it is the equivalent of Linux's `TCP_NODELAY`.
///
/// see <https://datatracker.ietf.org/doc/html/rfc896>
#[derive(Debug, Clone, Copy, PartialEq, Eq, From, Into)]
pub struct Nagle(bool);

/// By default uses [Nagle::enabled()], which is also the Linux default.
impl Default for Nagle {
    fn default() -> Self {
        Self::enabled()
    }
}

impl Nagle {
    /// Coalesce small writes, at the cost of extra latency.
    pub fn enabled() -> Self {
        Self(true)
    }

    /// Send every write out immediately, the equivalent of Linux's `TCP_NODELAY`.
    pub fn disabled() -> Self {
        Self(false)
    }

    /// Whether Nagle's Algorithm is enabled.
    pub fn is_enabled(self) -> bool {
        self.0
    }
}

/// Abort the connection when the peer stays silent for longer than the given duration.
///
/// The connection is aborted when the peer does not answer a connect attempt within the
/// duration, when it exceeds the duration between two packets while the transmit buffer is
/// not empty, or when it exceeds the duration between two packets while [KeepAlive] is set.
#[derive(Debug, Clone, Copy, PartialEq, Eq, From, Into)]
pub struct Timeout(Option<smoltcp::time::Duration>);

/// By default uses [Timeout::never()], an unresponsive peer never aborts the connection
/// on its own.
impl Default for Timeout {
    fn default() -> Self {
        Self::never()
    }
}

impl Timeout {
    /// Abort the connection after `duration` without a packet from the peer.
    pub fn after(duration: Duration) -> Self {
        Self(Some(duration.into()))
    }

    /// Never abort the connection because the peer is unresponsive.
    pub fn never() -> Self {
        Self(None)
    }

    /// The configured duration, [None] when the timeout is disabled.
    pub fn duration(self) -> Option<Duration> {
        self.0.map(Into::into)
    }
}

/// Probe an idle connection by sending a "keep-alive ACK" every interval.
///
/// Combined with [Timeout] this detects a peer that crashed without closing the connection:
/// a healthy peer answers with an ACK, a rebooted one with an RST, and a crashed one does not
/// answer at all and eventually trips the [Timeout].
#[derive(Debug, Clone, Copy, PartialEq, Eq, From, Into)]
pub struct KeepAlive(Option<smoltcp::time::Duration>);

/// By default uses [KeepAlive::disabled()], no probe is ever sent.
impl Default for KeepAlive {
    fn default() -> Self {
        Self::disabled()
    }
}

impl KeepAlive {
    /// Probe the peer after every `interval` without any communication.
    pub fn every(interval: Duration) -> Self {
        Self(Some(interval.into()))
    }

    /// Never probe an idle peer.
    pub fn disabled() -> Self {
        Self(None)
    }

    /// The configured interval, [None] when keep-alive is disabled.
    pub fn interval(self) -> Option<Duration> {
        self.0.map(Into::into)
    }
}

/// How long a pending ACK is held back so it can be coalesced with outgoing data or with a
/// later ACK.
///
/// Delaying ACKs saves packets but adds latency when the peer has [Nagle] enabled, since both
/// ends then wait on each other.
///
/// see <https://datatracker.ietf.org/doc/html/rfc1122#section-4.2.3.2>
#[derive(Debug, Clone, Copy, PartialEq, Eq, From, Into)]
pub struct AckDelay(Option<smoltcp::time::Duration>);

/// By default ACKs are delayed by [AckDelay::DEFAULT].
impl Default for AckDelay {
    fn default() -> Self {
        Self::after(Self::DEFAULT)
    }
}

impl AckDelay {
    /// The delay applied to a socket that never had this option set.
    pub const DEFAULT: Duration = Duration::from_millis(10);

    /// Hold a pending ACK back for at most `delay`.
    pub fn after(delay: Duration) -> Self {
        Self(Some(delay.into()))
    }

    /// Acknowledge every segment as soon as it is received.
    pub fn immediate() -> Self {
        Self(None)
    }

    /// The configured delay, [None] when every segment is acknowledged immediately.
    pub fn delay(self) -> Option<Duration> {
        self.0.map(Into::into)
    }
}

/// TCP timestamps, used for round trip time measurement and protection against wrapped
/// sequence numbers.
///
/// This option is write only: the underlying stack keeps the generator but never hands it
/// back, so there is no [crate::socket::option::GetSockOpt] counterpart.
///
/// see <https://datatracker.ietf.org/doc/html/rfc7323#section-3>
#[derive(Debug, Clone, Copy, Default, From, Into)]
pub struct Timestamp(Option<TcpTimestampGenerator>);

/// Alias for timestamp generation function
pub type TimestampGenerator = fn() -> u32;

impl Timestamp {
    /// Advertise TCP timestamps, taking the `TSval` of outgoing packets from `generator`.
    ///
    /// The generator must be monotonically increasing and should tick at a rate between 1Hz
    /// and 1MHz.
    pub fn enabled(generator: TimestampGenerator) -> Self {
        Self(Some(generator))
    }

    /// Do not advertise TCP timestamps. This is the default.
    pub fn disabled() -> Self {
        Self(None)
    }

    /// Whether TCP timestamps are advertised.
    pub fn is_enabled(self) -> bool {
        self.0.is_some()
    }
}

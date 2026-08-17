//! Userspace TCP/IP network stack providing (a)sync sockets and TUN interface

mod device;

pub mod error;

pub mod socket;

mod stack;
pub use stack::*;

mod config;
pub use config::{NetworkConfig, NetworkConfigV4, NetworkConfigV6};

mod utils;

use core::time;

pub mod rand {
    pub use proton_os_interface::rand::*;
    #[cfg(feature = "rng")]
    pub use rand_xoshiro as xoshiro;
}

/// A trait defining how to get an advisory deadline about when the next wake-up
/// must happen
pub trait Deadline {
    /// Get the advisory deadline w.r.t the current time in nanosec.
    ///
    /// If the deadline is None, then there is no expectation on the next
    /// wake-up
    fn next_deadline(&self, now_as_nanos: u64) -> Option<time::Duration>;
}

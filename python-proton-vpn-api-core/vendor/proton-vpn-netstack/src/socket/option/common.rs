use std::num::NonZeroU8;

#[derive(Debug, thiserror::Error)]
#[error("Hop limit must be non zero")]
pub struct InvalidHopLimit;

/// The TTL (hop-limit) for the socket
#[derive(Debug)]
pub struct HopLimit(NonZeroU8);

impl From<HopLimit> for u8 {
    fn from(value: HopLimit) -> Self {
        value.0.get()
    }
}

impl HopLimit {
    /// Create a new [HopLimit], fails if `hop_limit` is 0
    pub fn try_new(hop_limit: u8) -> Result<Self, InvalidHopLimit> {
        Ok(Self(NonZeroU8::new(hop_limit).ok_or(InvalidHopLimit)?))
    }
}

impl Default for HopLimit {
    /// Create a [HopLimit] with the default IANA value
    fn default() -> Self {
        /// see https://www.iana.org/assignments/ip-parameters/ip-parameters.xhtml
        const DEFAULT_IANA_SET_HOP_LIMIT: NonZeroU8 = NonZeroU8::new(64).unwrap();
        Self(DEFAULT_IANA_SET_HOP_LIMIT)
    }
}

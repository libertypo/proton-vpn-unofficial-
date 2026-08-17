use core::net;

type Prefix = u8;

#[derive(Debug, derive_more::From, Clone)]
pub struct NetworkConfigV4 {
    pub addr: net::Ipv4Addr,
    pub prefix: Prefix,
    pub gateway: Option<net::Ipv4Addr>,
}

#[derive(Debug, derive_more::From, Clone)]
pub struct NetworkConfigV6 {
    pub addr: net::Ipv6Addr,
    pub prefix: Prefix,
    pub gateway: Option<net::Ipv6Addr>,
}

#[derive(Debug, Clone, derive_more::From)]
pub enum NetworkConfig {
    V4(NetworkConfigV4),
    V6(NetworkConfigV6),
    Both(NetworkConfigV4, NetworkConfigV6),
}

impl NetworkConfig {
    pub fn into_parts(self) -> (Option<NetworkConfigV4>, Option<NetworkConfigV6>) {
        match self {
            NetworkConfig::V4(config) => (Some(config), None),
            NetworkConfig::V6(config) => (None, Some(config)),
            NetworkConfig::Both(v4, v6) => (Some(v4), Some(v6)),
        }
    }
}

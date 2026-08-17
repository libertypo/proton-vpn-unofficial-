use std::collections::VecDeque;

use derive_more::{Display, Error};
use tracing::trace;

const DEFAULT_MTU: u16 = 1500;

#[derive(Debug)]
pub struct Device {
    mtu: u16,
    rx_queue: VecDeque<Vec<u8>>,
    tx_queue: VecDeque<Vec<u8>>,
}

impl Default for Device {
    fn default() -> Self {
        Self {
            mtu: DEFAULT_MTU,
            tx_queue: Vec::with_capacity(Self::TX_CAP).into(),
            rx_queue: Vec::with_capacity(Self::RX_CAP).into(),
        }
    }
}

impl Device {
    const RX_CAP: usize = 2usize.pow(16);
    const TX_CAP: usize = 2usize.pow(16);

    /// Create a new device with the given MTU
    pub fn new(mtu: u16) -> Self {
        let mut this = Self::default();
        this.mtu = mtu;
        this
    }
}

#[derive(Debug, Display, Error, PartialEq, Eq)]
pub enum DeviceReadError {
    #[display("blocking call")]
    WouldBlock,
    #[display("not enough space")]
    NotEnoughSpace,
}

#[derive(Debug, Display, Error, PartialEq, Eq)]
pub enum DeviceWriteError {
    #[display("blocking call")]
    WouldBlock,
}

impl Device {
    #[cfg(feature = "std")]
    pub fn flush(&self) -> Result<(), DeviceWriteError> {
        if self.rx_queue.is_empty() {
            Ok(())
        } else {
            Err(DeviceWriteError::WouldBlock)
        }
    }

    pub fn read_packet(&mut self, buf: &mut [u8]) -> Result<usize, DeviceReadError> {
        let packet = self.tx_queue.front().ok_or(DeviceReadError::WouldBlock)?;

        if packet.len() > buf.len() {
            return Err(DeviceReadError::NotEnoughSpace);
        }

        let packet = self
            .tx_queue
            .pop_front()
            .ok_or(DeviceReadError::WouldBlock)?;

        let pkt_len = packet.len();

        buf[..pkt_len].copy_from_slice(&packet);

        Ok(pkt_len)
    }

    pub fn write_packet(&mut self, buf: &[u8]) -> Result<usize, DeviceWriteError> {
        if self.rx_queue.len() == Self::RX_CAP {
            trace!("device full");
            return Err(DeviceWriteError::WouldBlock);
        }

        self.rx_queue.push_back(buf.to_vec());

        Ok(buf.len())
    }
}

impl smoltcp::phy::Device for Device {
    type RxToken<'a>
        = RxToken
    where
        Self: 'a;
    type TxToken<'a>
        = TxToken<'a>
    where
        Self: 'a;

    fn capabilities(&self) -> smoltcp::phy::DeviceCapabilities {
        let mut caps = smoltcp::phy::DeviceCapabilities::default();
        caps.max_transmission_unit = self.mtu as usize;
        caps
    }

    fn receive(
        &mut self,
        _timestamp: smoltcp::time::Instant,
    ) -> Option<(Self::RxToken<'_>, Self::TxToken<'_>)> {
        self.rx_queue.pop_front().map(move |buffer| {
            let rx = RxToken { buffer };
            let tx = TxToken {
                queue: &mut self.tx_queue,
            };
            (rx, tx)
        })
    }

    fn transmit(&mut self, _timestamp: smoltcp::time::Instant) -> Option<Self::TxToken<'_>> {
        Some(TxToken {
            queue: &mut self.tx_queue,
        })
    }
}

pub struct RxToken {
    buffer: Vec<u8>,
}

impl smoltcp::phy::RxToken for RxToken {
    fn consume<R, F>(self, f: F) -> R
    where
        F: FnOnce(&[u8]) -> R,
    {
        f(&self.buffer)
    }
}

pub struct TxToken<'a> {
    queue: &'a mut VecDeque<Vec<u8>>,
}

impl smoltcp::phy::TxToken for TxToken<'_> {
    fn consume<R, F>(self, len: usize, f: F) -> R
    where
        F: FnOnce(&mut [u8]) -> R,
    {
        let mut buffer = vec![0; len];

        let result = f(&mut buffer);
        self.queue.push_back(buffer);

        result
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn read_from_device() {
        let mut device = Device::default();
        let mut buf = vec![];
        assert!(matches!(
            device.read_packet(&mut buf),
            Err(DeviceReadError::WouldBlock)
        ));
        assert_eq!(device.tx_queue.len(), 0);
        device.tx_queue.push_front(vec![0u8; 1024]);
        assert_eq!(device.tx_queue.len(), 1);
        assert!(matches!(
            device.read_packet(&mut buf),
            Err(DeviceReadError::NotEnoughSpace)
        ));
        assert_eq!(device.tx_queue.len(), 1);
        buf.resize(2048, 0);
        assert_eq!(device.read_packet(&mut buf).unwrap(), 1024);
        assert_eq!(device.tx_queue.len(), 0);
    }

    #[test]
    fn write_to_device() {
        let mut device = Device::default();
        let buf = vec![0u8; 1024];
        assert_eq!(device.rx_queue.len(), 0);
        for i in 0..2_i32.pow(16) {
            assert_eq!(device.write_packet(&buf).unwrap(), 1024);
            assert_eq!(device.rx_queue.len(), (i + 1) as usize);
        }
        assert_eq!(
            device.write_packet(&buf).unwrap_err(),
            DeviceWriteError::WouldBlock
        );
    }
}

# Proton VPN NetStack
This is an implementation of a userspace network stack. It uses [smoltcp](https://github.com/smoltcp-rs/smoltcp) as the low level TCP/IP stack.

## Use case to use this library
- You do not have access to an OS TCP/IP stack or you want to bypass yours
- You want userspace sockets with a common public interface (i.e., AsyncRead/Write, shutdown, close, etc.). Pluggable into Hyper and other popular libraries.
- You want to generate network layer packets (OSI layer 3)

## When you do not want to use this library
- You do have access to your OS TCP/IP and you can let it handle the TCP state machine as well as other socket management
- You build for your own platform (not multi-platform library).
- Performance is what matters the most

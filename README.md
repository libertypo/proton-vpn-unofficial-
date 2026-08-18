# Proton VPN GTK App Community Build

An independent community build of a native Proton VPN client for Arch Linux.

## License And Disclaimer

This project is released under GPL-3.0-or-later. It is an unofficial community
project and is not endorsed, sponsored, or sanctioned by Proton AG. Proton AG
retains copyright in its original work.

Use this software at your own risk.

## Run From The Checkout

The public checkout includes the local `python-proton-vpn-api-core` and ProTun
sources required by the launcher and package builders. No separate full Proton
checkout or manual source link is required.

Clone the repository, install the GTK runtime dependencies, then start the
launcher from the repository root:

```bash
git clone https://github.com/libertypo/proton-vpn-unofficial-.git
cd proton-vpn-unofficial-
sudo pacman -S --needed python python-gobject gtk4 networkmanager
./run-app.sh
```

The launcher verifies that the application and API-core modules resolve from the
checkout instead of mixed system packages. It also reports whether the installed
NetworkManager ProTun artifacts are available. Without those artifacts, the app
can run but Smart and Stealth protocols are unavailable.

Use the following diagnostic if the launcher reports an import-resolution issue:

```bash
./run-app.sh --diagnose-imports
```

## Build Packages (For The Brave At Heart)

This source tree targets Arch Linux and Manjaro. Building the complete local
package pair requires:

- `base-devel`, `git`, `rustup`, `cargo`, and `gnupg`
- Rust toolchain `1.93.1`
- NetworkManager development and runtime dependencies declared by the PKGBUILDs
- Network access to the Proton Rust registry configured in
	`python-proton-vpn-api-core/.cargo/config.toml`

Install the general build tools and pinned Rust toolchain:

```bash
sudo pacman -S --needed base-devel git rustup cargo gnupg
rustup toolchain install 1.93.1
```

The application requires the local API-core/ProTun package. Build that package
first, then build the GTK application package from the same checkout:

```bash
SIGN_PACKAGES=0 ./build-arch-local-api-core-package.sh -f
SIGN_PACKAGES=0 ./build-arch-proton-vpn-package.sh -f
```

The scripts write their artifacts to `dist/arch/` by default. They use a
low-memory build configuration by default; set `LOW_MEMORY_BUILD=0` to use the
normal parallel build settings. The `SIGN_PACKAGES=0` prefix intentionally
overrides the scripts' signing default and produces unsigned local test
artifacts; do not distribute them as release packages.

To produce signed release artifacts, omit `SIGN_PACKAGES=0`; this requires the
configured release key. Maintainers can inspect each script's accepted `makepkg`
options and environment variables with:

```bash
./build-arch-local-api-core-package.sh --script-help
./build-arch-proton-vpn-package.sh --script-help
```

## Testing Scope

This is a community build. Do not treat it as an official Proton or Manjaro
package. Test on a personally controlled, recoverable machine, not one where a
VPN or NetworkManager failure has meaningful consequences. Test installation,
login, connect/disconnect, Smart and Stealth behavior, split-tunneling behavior,
kill-switch behavior, and recovery or removal of test NetworkManager/ProTun
state.

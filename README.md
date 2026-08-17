# Proton VPN GTK App (Unofficial Build)

This effort is primarily for personal use. Proton has not shown interest in adopting these changes, so this project continues as an independent modification.

## Overview

The main change introduced here is support for theme sensitivity, including light, dark, and automatic modes. Proton implemented this capability in their other applications, but it was not available in this one.

## Notes

All available automated and manual efforts have been used to squash bugs and improve stability. However, as with any project, bugs may still remain.

If you use this build and encounter issues, please report them through the issue report feature in the application.

## Recommended usage model

This repository is intentionally shell-first and distro-first.

- For users: install or build using your distro packaging flow.
- For contributors and advanced users: run directly from source with the provided script.

Generic pip-only installs are not the primary user workflow for this project.

## Run directly from source

```bash
./run-app.sh
```

You can pass app arguments too:

```bash
./run-app.sh --help
```

The script checks for required GTK bindings and prints distro package hints if they are missing.

## Build for distro packaging

The repository includes distro packaging assets, such as:

- `manjaro/PKGBUILD`

For Manjaro/Arch-style packaging:

```bash
cd manjaro
makepkg -si
```

## Security Scanning

The CI workflow for this repository runs security scanning with:
- Semgrep
- Trivy
- Gitleaks

If you maintain these tools locally, you can run equivalent checks directly:

```bash
semgrep --config auto proton/
trivy fs --severity HIGH,CRITICAL .
gitleaks detect --source . --no-git
```

## Testing Status

This remote branch currently includes limited test artifacts and does not ship
the full local test/security helper scripts referenced in earlier revisions.

## Disclaimer

This is a private, unofficial effort and is not sanctioned by Proton. The original copyright remains with Proton AG under the GPL-3 license.

All reasonable efforts have been made to deliver a bug-free experience, but no software is perfect. By using this application, you accept responsibility for any damage, loss, or unintended consequences that may result from its use.

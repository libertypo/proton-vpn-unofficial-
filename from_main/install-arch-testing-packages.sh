#!/usr/bin/env bash
set -euo pipefail

PACKAGE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EXPECTED_FINGERPRINT="B2B8E2F00629B64ADEEBC5AF7903F448BD7BBBF1"
PUBLIC_KEY="$PACKAGE_DIR/libertypo-release-key.asc"

if [[ ! -f "$PUBLIC_KEY" && -f "$PACKAGE_DIR/dist/arch/libertypo-release-key.asc" ]]; then
  PACKAGE_DIR="$PACKAGE_DIR/dist/arch"
  PUBLIC_KEY="$PACKAGE_DIR/libertypo-release-key.asc"
fi

if ! command -v pkexec >/dev/null 2>&1; then
  echo "Polkit is required for the graphical installer." >&2
  exit 1
fi

for command_name in pacman pacman-key sha256sum; do
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "This installer requires the Arch Linux command: $command_name." >&2
    exit 1
  fi
done

if ! command -v gpg >/dev/null 2>&1; then
  echo "GnuPG is required for package verification." >&2
  exit 1
fi

if [[ ! -f "$PUBLIC_KEY" ]]; then
  echo "The bundled public signing key is missing." >&2
  exit 1
fi

actual_fingerprint="$(gpg --batch --show-keys --with-colons "$PUBLIC_KEY" | awk -F: '$1 == "fpr" {print $10; exit}')"
if [[ "$actual_fingerprint" != "$EXPECTED_FINGERPRINT" ]]; then
  echo "The bundled signing key fingerprint does not match the expected release key." >&2
  exit 1
fi

VERIFY_HOME="$(mktemp -d)"
chmod 700 "$VERIFY_HOME"
trap 'rm -rf "$VERIFY_HOME"' EXIT
gpg --batch --homedir "$VERIFY_HOME" --import "$PUBLIC_KEY" >/dev/null 2>&1

mapfile -t nm_packages < <(find "$PACKAGE_DIR" -maxdepth 1 -type f -name 'nm-protun-artifacts-unofficial-*.pkg.tar.zst' | sort)
if [[ ${#nm_packages[@]} -ne 1 ]]; then
  echo "The installer requires exactly one nm-protun package." >&2
  exit 1
fi
nm_package="${nm_packages[0]}"

echo "Signing key: libertypo <libertypo@proton.me>"
echo "Fingerprint: $EXPECTED_FINGERPRINT"
echo "The installer will add this key to pacman's trusted keyring."

confirmation_text="Trust this signing key and install the nm-protun package?\n\nlibertypo <libertypo@proton.me>\nFingerprint: $EXPECTED_FINGERPRINT"
if command -v zenity >/dev/null 2>&1; then
  if ! zenity --question --title="Proton VPN package signing key" --text="$confirmation_text" --width=520; then
    echo "Installation cancelled." >&2
    exit 1
  fi
elif command -v kdialog >/dev/null 2>&1; then
  if ! kdialog --yesno "$confirmation_text" --title "Proton VPN package signing key"; then
    echo "Installation cancelled." >&2
    exit 1
  fi
else
  printf '\nType ACCEPT to trust this key and continue: '
  read -r confirmation
  [[ "$confirmation" == "ACCEPT" ]] || {
    echo "Installation cancelled." >&2
    exit 1
  }
fi

for package in "$nm_package"; do
  checksum_file="$package.sha256"
  signature_file="$package.sig"
  [[ -f "$checksum_file" && -f "$signature_file" ]] || {
    printf 'Verification files are missing for %s.\n' "$(basename "$package")" >&2
    exit 1
  }
  (cd "$(dirname "$package")" && sha256sum -c "$(basename "$checksum_file")")
  gpg --batch --homedir "$VERIFY_HOME" --verify "$signature_file" "$package" >/dev/null
done

key_path="$PUBLIC_KEY"
nm_path="$nm_package"
pkexec env KEY_PATH="$key_path" NM_PATH="$nm_path" bash -c '
  set -euo pipefail
  pacman-key --add "$KEY_PATH"
  pacman-key --lsign-key B2B8E2F00629B64ADEEBC5AF7903F448BD7BBBF1

  pacman -U --needed "$NM_PATH"
'

echo "Proton VPN testing packages installed successfully."

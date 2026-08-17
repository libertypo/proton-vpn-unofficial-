#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
API_CORE_DIR="$ROOT_DIR/python-proton-vpn-api-core"
OUT_DIR="${OUT_DIR:-$ROOT_DIR/dist/arch}"
WORK_DIR="${WORK_DIR:-$(mktemp -d)}"
KEEP_WORKDIR="${KEEP_WORKDIR:-0}"
LOW_MEMORY_BUILD="${LOW_MEMORY_BUILD:-1}"
GPG_KEY="${GPG_KEY:-B2B8E2F00629B64ADEEBC5AF7903F448BD7BBBF1}"
RUST_TOOLCHAIN="${RUST_TOOLCHAIN:-+1.93.1}"
SIGN_PACKAGES="${SIGN_PACKAGES:-1}"

if [[ "${1:-}" == "--script-help" ]]; then
  cat <<'EOF'
Build the local Proton VPN API-core and nm-protun Arch package.

Usage:
  ./build-arch-local-api-core-package.sh [makepkg options]

Environment variables:
  OUT_DIR          Output directory (default: dist/arch)
  WORK_DIR        Temporary build directory
  KEEP_WORKDIR    Set to 1 to keep WORK_DIR
  LOW_MEMORY_BUILD Set to 1 (default) for cargo -j1 and low-memory compression
  GPG_KEY         Signing-key fingerprint
  RUST_TOOLCHAIN  Rustup toolchain selector (default: +1.93.1)
EOF
  exit 0
fi

for command_name in cargo makepkg sha256sum tar; do
  command -v "$command_name" >/dev/null 2>&1 || {
    echo "error: required command not found: $command_name" >&2
    exit 1
  }
done
if [[ "$SIGN_PACKAGES" == "1" ]]; then
  command -v gpg >/dev/null 2>&1 || { echo "error: required command not found: gpg" >&2; exit 1; }
fi

[[ -d "$API_CORE_DIR" && -f "$API_CORE_DIR/setup.py" && -f "$API_CORE_DIR/Cargo.toml" ]] || {
  echo "error: local API-core source tree is incomplete: $API_CORE_DIR" >&2
  exit 1
}
if [[ "$SIGN_PACKAGES" == "1" ]] && ! gpg --batch --list-secret-keys "$GPG_KEY" >/dev/null 2>&1; then
  echo "error: signing key is not available: $GPG_KEY" >&2
  exit 1
fi

if [[ "$KEEP_WORKDIR" != "1" ]]; then
  trap 'rm -rf "$WORK_DIR"' EXIT
fi

mkdir -p "$OUT_DIR"
gpg --batch --export-options export-minimal --armor --export "$GPG_KEY" > "$OUT_DIR/libertypo-release-key.asc"
cp "$ROOT_DIR/install-arch-testing-packages.sh" "$OUT_DIR/install-arch-testing-packages.sh"
chmod 755 "$OUT_DIR/install-arch-testing-packages.sh"
rm -f "$OUT_DIR"/nm-protun-artifacts-unofficial-*.pkg.tar.*
rm -f "$OUT_DIR"/python-proton-vpn-api-core-local-*.pkg.tar.*
PKGDIR="$WORK_DIR/python-proton-vpn-api-core-local"
SOURCE_DIR="python-proton-vpn-api-core-local-5.5.11"
mkdir -p "$PKGDIR/source/$SOURCE_DIR"

if [[ -n "$RUST_TOOLCHAIN" ]]; then
  cargo_command=(cargo "$RUST_TOOLCHAIN")
else
  cargo_command=(cargo)
fi

if [[ "$LOW_MEMORY_BUILD" == "1" ]]; then
  cargo_args=(--locked --release --features 'protun nm_protun_auth_dialog' -j1)
else
  cargo_args=(--locked --release --features 'protun nm_protun_auth_dialog')
fi
(
  cd "$API_CORE_DIR"
  "${cargo_command[@]}" build "${cargo_args[@]}"
)

cp -a "$API_CORE_DIR/." "$PKGDIR/source/$SOURCE_DIR/"
rm -rf "$PKGDIR/source/$SOURCE_DIR/target" "$PKGDIR/source/$SOURCE_DIR/.git"
find "$PKGDIR/source/$SOURCE_DIR" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$PKGDIR/source/$SOURCE_DIR" -type f -name '*.pyc' -delete
cp -f "$API_CORE_DIR/target/release/nm-protun-service" "$PKGDIR/source/$SOURCE_DIR/resources/nm-protun-service"
cp -f "$API_CORE_DIR/target/release/nm-protun-auth-dialog" "$PKGDIR/source/$SOURCE_DIR/resources/nm-protun-auth-dialog"

cat > "$PKGDIR/source/$SOURCE_DIR/nm-protun.service" <<'UNIT'
[Unit]
Description=NetworkManager ProTun service
After=NetworkManager.service
PartOf=NetworkManager.service

[Service]
Type=dbus
BusName=org.freedesktop.NetworkManager.protun
ExecStart=/usr/libexec/nm-protun-service
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT

GZIP=-n tar \
  --sort=name \
  --mtime='UTC 1970-01-01' \
  --owner=0 \
  --group=0 \
  --numeric-owner \
  -czf "$PKGDIR/$SOURCE_DIR.tar.gz" \
  -C "$PKGDIR/source" \
  "$SOURCE_DIR"
SOURCE_HASH="$(sha256sum "$PKGDIR/$SOURCE_DIR.tar.gz" | awk '{print $1}')"
PACKAGING_DIR="$ROOT_DIR/packaging/arch/python-proton-vpn-api-core-local"
cp "$PACKAGING_DIR/PKGBUILD" "$PKGDIR/PKGBUILD"
sed -i "s/SOURCE_HASH/$SOURCE_HASH/" "$PKGDIR/PKGBUILD"
(
  cd "$PKGDIR"
  if [[ "$LOW_MEMORY_BUILD" == "1" ]]; then
    cat > makepkg.lowmem.conf <<'EOF'
source /etc/makepkg.conf
MAKEFLAGS="-j1"
COMPRESSZST=(zstd -c -T1 -3 -)
PKGEXT='.pkg.tar.zst'
EOF
    MAKEFLAGS="-j1" makepkg --config "$PKGDIR/makepkg.lowmem.conf" -f "$@"
  else
    makepkg -f "$@"
  fi
)

packages=()
for artifact in "$PKGDIR"/*.pkg.tar.*; do
  [[ -f "$artifact" && "$artifact" =~ \.pkg\.tar\.[^.]+$ ]] || continue
  packages+=("$artifact")
done
[[ ${#packages[@]} -eq 1 ]] || {
  echo "error: expected one local API-core package archive" >&2
  exit 1
}
package="${packages[0]}"
package_name="$(basename "$package")"
cp -f "$package" "$OUT_DIR/$package_name"
(
  cd "$OUT_DIR"
  sha256sum "$package_name" > "$package_name.sha256"
  if [[ "$SIGN_PACKAGES" == "1" ]]; then
    gpg --batch --yes --local-user "$GPG_KEY" --detach-sign --output "$package_name.sig" "$package_name"
  else
    rm -f "$package_name.sig"
  fi
)
gpg --batch --export-options export-minimal --armor --export "$GPG_KEY" > "$OUT_DIR/libertypo-release-key.asc"
echo "==> Package copied to: $OUT_DIR/$package_name"

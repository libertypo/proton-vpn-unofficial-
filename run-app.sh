#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"
API_CORE_DIR="$ROOT_DIR/python-proton-vpn-api-core"

if ! command -v python3 >/dev/null 2>&1; then
	echo "error: python3 was not found in PATH" >&2
	exit 1
fi

if ! python3 - <<'PY' >/dev/null 2>&1
import gi  # noqa: F401
from gi.repository import Gtk  # noqa: F401
PY
then
	cat >&2 <<'EOF'
error: missing GTK Python bindings (gi.repository)

Install your distro package for PyGObject/GTK, then rerun this script.
Examples:
	Arch/Manjaro: sudo pacman -S python-gobject gtk4
	Debian/Ubuntu: sudo apt install python3-gi gir1.2-gtk-4.0
EOF
	exit 1
fi

if [[ ! -d "$API_CORE_DIR/proton/vpn/backend/networkmanager/core" ]]; then
	cat >&2 <<EOF
error: missing runtime sources under $API_CORE_DIR

This launcher expects a checkout that includes python-proton-vpn-api-core,
otherwise Python may silently import installed system packages and mix versions.
EOF
	exit 1
fi

export ROOT_DIR
export API_CORE_DIR
export PYTHONPATH="$API_CORE_DIR:$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

if [[ "${1:-}" == "--diagnose-imports" ]]; then
	python3 - <<'PY'
import importlib
import sys

for module_name in (
	"proton.vpn.app.gtk",
	"proton.vpn.backend.networkmanager.core.networkmanager",
	"proton.vpn.backend.networkmanager.protocol.protun.protun",
):
	module = importlib.import_module(module_name)
	print(f"{module_name}: {module.__file__}")

print("PYTHONPATH:")
for path in sys.path:
	print(f"  {path}")
PY
	exit 0
fi

if ! python3 - <<'PY'
import importlib
import os

root = os.environ["ROOT_DIR"]
api_core = os.environ["API_CORE_DIR"]
allowed_prefixes = (root + "/", api_core + "/")

for module_name in (
	"proton.vpn.app.gtk.__main__",
	"proton.vpn.backend.networkmanager.core.networkmanager",
):
	module = importlib.import_module(module_name)
	module_file = getattr(module, "__file__", "") or ""
	if not any(module_file.startswith(prefix) for prefix in allowed_prefixes):
		raise SystemExit(
			f"error: module '{module_name}' resolved outside checkout: {module_file}"
		)
PY
then
	cat >&2 <<'EOF'
error: Proton modules are resolving from installed system packages instead of this checkout.

Use this script from the repository root, and ensure python-proton-vpn-api-core
is present next to run-app.sh.
EOF
	exit 1
fi

required_nm_protun_paths=(
	"/usr/libexec/nm-protun-service"
	"/usr/libexec/nm-protun-auth-dialog"
	"/usr/lib/NetworkManager/VPN/nm-protun.name"
	"/usr/share/dbus-1/system.d/nm-protun-service.conf"
)

missing_nm_protun_paths=()
for p in "${required_nm_protun_paths[@]}"; do
	if [[ ! -f "$p" ]]; then
		missing_nm_protun_paths+=("$p")
	fi
done

if [[ ${#missing_nm_protun_paths[@]} -gt 0 ]]; then
	if [[ "${PROTONVPN_REQUIRE_NM_PROTUN:-0}" == "1" ]]; then
		{
			echo "error: required nm-protun artifacts are missing:"
			for p in "${missing_nm_protun_paths[@]}"; do
				echo "  - $p"
			done
			echo
			echo "Set PROTONVPN_REQUIRE_NM_PROTUN=0 or unset it to run without Smart/Stealth."
		} >&2
		exit 1
	fi

	{
		echo "warning: nm-protun artifacts are not fully installed."
		for p in "${missing_nm_protun_paths[@]}"; do
			echo "  - missing: $p"
		done
		echo "Smart/Stealth protocols will be unavailable until these artifacts are installed."
	} >&2
fi

exec python3 -m proton.vpn.app.gtk "$@"

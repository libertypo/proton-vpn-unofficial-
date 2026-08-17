#!/usr/bin/env bash
set -euo pipefail

MOUNT_POINT="${MOUNT_POINT:-/mnt/hgfs}"

if ! command -v vmhgfs-fuse >/dev/null 2>&1; then
  echo "error: vmhgfs-fuse is not installed. Install open-vm-tools first." >&2
  exit 1
fi

if [[ ! -d /mnt/hgfs ]]; then
  sudo mkdir -p "$MOUNT_POINT"
fi

if mountpoint -q "$MOUNT_POINT"; then
  echo "VMware shared folders are already mounted at: $MOUNT_POINT"
else
  sudo vmhgfs-fuse .host:/ "$MOUNT_POINT" -o allow_other
  echo "VMware shared folders mounted at: $MOUNT_POINT"
fi

find "$MOUNT_POINT" -mindepth 1 -maxdepth 1 -type d -printf '%f\n'

#!/bin/bash
# Mounts TrueNAS SMB share and rsyncs petascale data (SQLite + Parquet archive).
# Runs as a systemd service triggered by petascale-backup.timer.
set -euo pipefail

MOUNT=/mnt/truenas-petascale
CREDS=/etc/petascale-smb-credentials
SRC=/data/petascale/
SHARE=//truenas.local/petascale

mount_share() {
    if mountpoint -q "$MOUNT"; then
        return 0
    fi
    mkdir -p "$MOUNT"
    mount -t cifs "$SHARE" "$MOUNT" \
        --options credentials="$CREDS",uid=0,gid=0,file_mode=0660,dir_mode=0770,vers=3.0
}

umount_share() {
    if mountpoint -q "$MOUNT"; then
        umount "$MOUNT"
    fi
}

trap umount_share EXIT

mount_share

# Sync SQLite db and Parquet archive; skip the dashboard HTML (regenerated locally)
rsync -av --exclude='dashboard.html' \
    "$SRC" "$MOUNT/"

echo "$(date -Iseconds) backup complete"

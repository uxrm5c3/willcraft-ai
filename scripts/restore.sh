#!/bin/bash
# WillCraft restore script — inverse of backup.sh.
#
# Usage:
#   ./restore.sh /path/to/willcraft_2026-05-12T02-30-00Z.tar.gz
#
# This OVERWRITES the live DB + uploads. It will:
#   1. Stop the willcraft-web container
#   2. Extract the tarball into a staging dir
#   3. Copy DB + clients/ + quarantine/ into /app/data/
#   4. Start the container
#
# Always test on a staging box first. Don't run on prod without a recent
# off-box backup of the CURRENT state in case the restore is wrong.

set -euo pipefail

if [ $# -ne 1 ]; then
    echo "Usage: $0 <willcraft_TIMESTAMP.tar.gz>"
    exit 1
fi

TARBALL="$1"
if [ ! -f "$TARBALL" ]; then
    echo "Tarball not found: $TARBALL"
    exit 1
fi

WILLCRAFT_DIR="${WILLCRAFT_DIR:-/home/ubuntu/willcraft}"
CONTAINER="${BACKUP_CONTAINER:-willcraft-web}"
STAGING=$(mktemp -d)
trap "rm -rf $STAGING" EXIT

echo "Extracting $TARBALL → $STAGING …"
tar -xzf "$TARBALL" -C "$STAGING"
INNER=$(ls "$STAGING" | head -1)
SNAP="$STAGING/$INNER"
echo "Snapshot dir: $SNAP"
ls -la "$SNAP"

echo "Stopping $CONTAINER …"
docker stop "$CONTAINER" || true

# Find the docker volume mountpoint
DATA_HOST=$(docker volume inspect -f '{{.Mountpoint}}' "$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/app/data" }}{{ .Name }}{{ end }}{{ end }}' "$CONTAINER")" 2>/dev/null || true)
if [ -z "$DATA_HOST" ]; then
    # Bind mount fallback
    DATA_HOST=$(docker inspect -f '{{ range .Mounts }}{{ if eq .Destination "/app/data" }}{{ .Source }}{{ end }}{{ end }}' "$CONTAINER")
fi
echo "Data dir on host: $DATA_HOST"

if [ -f "$SNAP/willcraft.db" ]; then
    echo "Restoring DB → $DATA_HOST/willcraft.db"
    sudo cp "$SNAP/willcraft.db" "$DATA_HOST/willcraft.db"
fi
for f in clients quarantine; do
    if [ -f "$SNAP/$f.tgz" ]; then
        echo "Restoring $f/ → $DATA_HOST/"
        sudo tar -xzf "$SNAP/$f.tgz" -C "$DATA_HOST"
    fi
done

echo "Starting $CONTAINER …"
docker start "$CONTAINER"
sleep 3
docker ps --filter "name=$CONTAINER"

echo "Restore complete. Verify via: curl https://will.alantanjb.com/api/health"

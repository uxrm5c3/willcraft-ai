#!/bin/bash
# WillCraft daily backup script.
#
# Captures three things into ONE timestamped tarball:
#   1. SQLite DB (via `.backup` so app keeps running, no lock contention)
#   2. /app/data/clients/    — every user's uploads + generated docs
#   3. /app/data/quarantine/ — emails that didn't match a client (§10x.234)
#
# Snapshots land in $BACKUP_LOCAL_DIR (default /home/ubuntu/willcraft-backups)
# and rotate after $BACKUP_KEEP_DAYS days (default 14).
#
# Off-box copy (PICK ONE — none configured = local-only):
#   BACKUP_RSYNC_DEST   e.g. user@backup.example.com:/var/backups/willcraft
#   BACKUP_S3_BUCKET    e.g. s3://my-bucket/willcraft  (requires `aws` CLI)
#   BACKUP_B2_BUCKET    e.g. b2://my-bucket            (requires `b2` CLI)
#
# Run via cron daily at 02:30 UTC (set up by deploy-setup.sh).

set -euo pipefail

BACKUP_LOCAL_DIR="${BACKUP_LOCAL_DIR:-/home/ubuntu/willcraft-backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
WILLCRAFT_DIR="${WILLCRAFT_DIR:-/home/ubuntu/willcraft}"
CONTAINER="${BACKUP_CONTAINER:-willcraft-web}"

STAMP=$(date -u +'%Y-%m-%dT%H-%M-%SZ')
SNAPSHOT="$BACKUP_LOCAL_DIR/willcraft_$STAMP"
LOG="$BACKUP_LOCAL_DIR/backup.log"

mkdir -p "$BACKUP_LOCAL_DIR"

# Log helper — appends to log AND echoes to stdout (so cron mail catches it).
log() {
    echo "[$(date -u +'%Y-%m-%dT%H:%M:%SZ')] $*" | tee -a "$LOG"
}

log "=== Starting WillCraft backup → $SNAPSHOT ==="

mkdir -p "$SNAPSHOT"

# --- (1) SQLite hot backup ----------------------------------------------------
# `.backup` is the SQLite-recommended way to snapshot a live DB without
# corrupting it. The app keeps serving traffic during the copy.
log "Snapshotting SQLite DB via .backup …"
if docker exec "$CONTAINER" \
    sqlite3 /app/data/willcraft.db ".backup /app/data/willcraft.db.bak"; then
    docker cp "$CONTAINER:/app/data/willcraft.db.bak" "$SNAPSHOT/willcraft.db"
    docker exec "$CONTAINER" rm -f /app/data/willcraft.db.bak
    log "  → DB snapshot OK ($(stat -c%s "$SNAPSHOT/willcraft.db") bytes)"
else
    log "  → DB snapshot FAILED"
    exit 1
fi

# --- (2) Client uploads -------------------------------------------------------
log "Snapshotting client uploads + quarantine …"
docker exec "$CONTAINER" sh -c '
    cd /app/data
    [ -d clients ]    && tar -czf /tmp/clients.tgz    clients    || true
    [ -d quarantine ] && tar -czf /tmp/quarantine.tgz quarantine || true
'
for f in clients quarantine; do
    if docker exec "$CONTAINER" test -f "/tmp/$f.tgz"; then
        docker cp "$CONTAINER:/tmp/$f.tgz" "$SNAPSHOT/$f.tgz"
        docker exec "$CONTAINER" rm -f "/tmp/$f.tgz"
        log "  → $f.tgz $(stat -c%s "$SNAPSHOT/$f.tgz") bytes"
    fi
done

# --- (3) Code revision pin ----------------------------------------------------
if [ -d "$WILLCRAFT_DIR/.git" ]; then
    (cd "$WILLCRAFT_DIR" && git rev-parse HEAD 2>/dev/null) > "$SNAPSHOT/git_HEAD.txt" || true
    log "  → recorded git HEAD: $(cat "$SNAPSHOT/git_HEAD.txt" 2>/dev/null || echo unknown)"
fi

# --- (4) Compress whole snapshot ---------------------------------------------
log "Compressing snapshot …"
TARBALL="$BACKUP_LOCAL_DIR/willcraft_$STAMP.tar.gz"
tar -czf "$TARBALL" -C "$BACKUP_LOCAL_DIR" "willcraft_$STAMP"
rm -rf "$SNAPSHOT"
SZ=$(stat -c%s "$TARBALL")
log "  → final tarball: $TARBALL ($SZ bytes)"

# --- (5) Validate ------------------------------------------------------------
log "Validating tarball …"
if tar -tzf "$TARBALL" >/dev/null 2>&1; then
    log "  → tarball integrity OK"
else
    log "  → tarball integrity FAILED — keeping for inspection"
    exit 2
fi

# --- (6) Off-box copy (best-effort) ------------------------------------------
if [ -n "${BACKUP_RSYNC_DEST:-}" ]; then
    log "Off-box rsync → $BACKUP_RSYNC_DEST"
    if rsync -avz --partial "$TARBALL" "$BACKUP_RSYNC_DEST"/; then
        log "  → rsync OK"
    else
        log "  → rsync FAILED"
    fi
fi
if [ -n "${BACKUP_S3_BUCKET:-}" ]; then
    log "Off-box upload → $BACKUP_S3_BUCKET"
    if aws s3 cp "$TARBALL" "$BACKUP_S3_BUCKET/"; then
        log "  → S3 upload OK"
    else
        log "  → S3 upload FAILED"
    fi
fi
if [ -n "${BACKUP_B2_BUCKET:-}" ]; then
    log "Off-box upload → $BACKUP_B2_BUCKET"
    BNAME=$(basename "$TARBALL")
    if b2 upload-file "${BACKUP_B2_BUCKET#b2://}" "$TARBALL" "$BNAME"; then
        log "  → B2 upload OK"
    else
        log "  → B2 upload FAILED"
    fi
fi
if [ -z "${BACKUP_RSYNC_DEST:-}${BACKUP_S3_BUCKET:-}${BACKUP_B2_BUCKET:-}" ]; then
    log "No off-box destination configured (BACKUP_RSYNC_DEST / BACKUP_S3_BUCKET / BACKUP_B2_BUCKET)."
    log "Snapshot kept locally only — set one of those env vars in ~/willcraft/.env to enable."
fi

# --- (7) Rotate --------------------------------------------------------------
log "Rotating snapshots older than $BACKUP_KEEP_DAYS days …"
find "$BACKUP_LOCAL_DIR" -name 'willcraft_*.tar.gz' -type f \
     -mtime "+$BACKUP_KEEP_DAYS" -print -delete | tee -a "$LOG" || true

log "=== Backup complete ==="

#!/usr/bin/env bash
# kakkava_brain_sync.sh — sync OneDrive/Kakkava Sofia into the SocialAuto
# company brain (Chroma team_*_brain + Nextcloud note).
#
# Manifest-gated: builds a file list+size+mtime manifest of the source
# folder; only re-ingests when it changed since the last successful run.
# Idempotent — doc_ids are path-derived, so re-runs upsert cleanly.
#
# Cron-safe: exports PATH, uses PLATFORM_FIT_REPO_ROOT-style override via
# KAKKAVA_SRC / KAKKAVA_REPO env vars. Safe to run anytime; exits 0 with a
# clear message when the stack or source is unavailable.
set -euo pipefail

export PATH="/usr/local/bin:/usr/bin:/bin:$PATH"

SRC="${KAKKAVA_SRC:-/mnt/c/Users/tbaltzakis/OneDrive/Kakkava Sofia}"
REPO="${KAKKAVA_REPO:-/home/tbaltzakis/cu130-slim}"
STATE_DIR="${KAKKAVA_STATE:-$HOME/.local/state/kakkava-brain-sync}"
MANIFEST="$STATE_DIR/manifest.txt"
LOG_PREFIX="[kakkava-brain-sync]"

mkdir -p "$STATE_DIR" "${TMPDIR:-/tmp}"

if [ ! -d "$SRC" ]; then
    echo "$LOG_PREFIX source not found: $SRC" >&2
    exit 0
fi
if ! docker info >/dev/null 2>&1; then
    echo "$LOG_PREFIX docker daemon down — skipping" >&2
    exit 0
fi
if ! docker ps --format '{{.Names}}' | grep -qx social-api; then
    echo "$LOG_PREFIX social-api not running — skipping" >&2
    exit 0
fi

# Manifest: relpath + size + mtime for every file (sorted).
NEW_MANIFEST=$(mktemp)
( cd "$SRC" && find . -type f -printf '%p\t%s\t%T@\n' | sort ) > "$NEW_MANIFEST"

if [ -f "$MANIFEST" ] && cmp -s "$MANIFEST" "$NEW_MANIFEST"; then
    rm -f "$NEW_MANIFEST"
    echo "$LOG_PREFIX no changes — $(wc -l < "$MANIFEST") files unchanged"
    exit 0
fi

TEAM_ID=$(docker exec social-postgres psql -U social_user -d social_automation \
    -tAc "SELECT id FROM teams WHERE name ILIKE '%baltzakis%' LIMIT 1" | tr -d '[:space:]')
if [ -z "$TEAM_ID" ]; then
    echo "$LOG_PREFIX could not resolve team_id" >&2
    exit 1
fi

INGEST_PY="$REPO/.devin/skills/kakkava-brain-sync/scripts/kakkava_brain_ingest.py"

cleanup() {
    docker exec -u root social-api rm -rf /tmp/kakkava-sofia \
        /tmp/kakkava_brain_ingest.py 2>/dev/null || true
}
trap cleanup EXIT

# docker cp handles the recursive copy itself — tar over /mnt/c (DrvFs)
# intermittently fails with "Cannot savedir: Cannot allocate memory".
echo "$LOG_PREFIX manifest changed — copying $SRC into social-api"
docker exec -u root social-api rm -rf /tmp/kakkava-sofia
docker cp "$SRC" social-api:/tmp/kakkava-sofia
docker cp "$INGEST_PY" social-api:/tmp/kakkava_brain_ingest.py

echo "$LOG_PREFIX ingesting into team $TEAM_ID"
docker exec -e SOCIALAUTO_TEAM_ID="$TEAM_ID" -e KAKKAVA_ROOT=/tmp/kakkava-sofia \
    social-api python3 /tmp/kakkava_brain_ingest.py

mv "$NEW_MANIFEST" "$MANIFEST"
echo "$LOG_PREFIX done — manifest updated"

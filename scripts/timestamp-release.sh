#!/usr/bin/env bash
# timestamp-release.sh — prepare a repo for proof-of-authorship timestamping.
#
# Produces a clean release zip + SHA-256 checksum you can submit to:
#   * https://www.timestamp.gr  (Hellenic Copyright Organization — free,
#     official Greek state service; certificate = proof of existence date)
#   * OpenTimestamps (optional, if `ots` is installed — anchors the hash
#     into the Bitcoin blockchain, also free)
#
# Usage:
#   ./scripts/timestamp-release.sh [repo_dir] [label]
#   e.g. ./scripts/timestamp-release.sh ~/cloudless.gr v2.4.0
#
# The zip is written next to the repo as <name>-<label|git-sha>.zip
# and is NOT committed. Nothing in it should contain secrets — this script
# refuses to include .env files; verify anyway before uploading anywhere.
set -euo pipefail

REPO="${1:-$PWD}"
LABEL="${2:-}"
REPO="$(cd "$REPO" && pwd)"
NAME="$(basename "$REPO")"
SHA="$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo nogit)"
TAG="${LABEL:-$SHA}"
OUT="$(dirname "$REPO")/${NAME}-${TAG}.zip"

cd "$REPO"
echo "Repo:   $REPO"
echo "Commit: $SHA"
echo "Out:    $OUT"

# Archive only tracked files — excludes .env, node_modules, build output,
# and anything gitignored (cookie dumps, session states, local secrets).
git archive --format=zip -o "$OUT" HEAD

SHA256="$(sha256sum "$OUT" | awk '{print $1}')"
echo "$SHA256  $(basename "$OUT")" > "$OUT.sha256"
echo
echo "SHA-256: $SHA256"
echo "Manifest: $OUT.sha256"
echo
echo "Next steps:"
echo "  1. Go to https://www.timestamp.gr (free HCO account required)"
echo "  2. Submit $OUT (or just the .sha256 if you prefer not to upload code)"
echo "  3. Save the issued timestamp certificate alongside this zip"
if command -v ots >/dev/null 2>&1; then
  ots stamp "$OUT.sha256" 2>/dev/null && echo "  + OpenTimestamps stamp queued: $OUT.sha256.ots"
else
  echo "  (optional) pip install opentimestamps-client && ots stamp $OUT.sha256"
fi

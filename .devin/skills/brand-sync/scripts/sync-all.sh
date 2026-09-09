#!/usr/bin/env bash
# Sync brand to ALL connected profiles (bio + name + picture where supported).
# Usage: sync-all.sh
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

echo "=== Brand Sync: All Platforms ==="
echo ""

# 1. Show current status
echo "--- Current Status ---"
bash "$ROOT/.devin/skills/brand-sync/scripts/sync-status.sh"
echo ""

# 2. Sync bio to all platforms
echo "--- Syncing Bios ---"
bash "$ROOT/.devin/skills/brand-sync/scripts/sync-bio.sh"
echo ""

# 3. Sync profile pictures
echo "--- Syncing Profile Pictures ---"
bash "$ROOT/.devin/skills/brand-sync/scripts/sync-profile-pic.sh"
echo ""

# 4. Show final status
echo "--- Final Status ---"
bash "$ROOT/.devin/skills/brand-sync/scripts/sync-status.sh"

echo ""
echo "=== Brand sync complete ==="

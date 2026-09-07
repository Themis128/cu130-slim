#!/usr/bin/env bash
# Add professional links to ALL connected social accounts
# Website: https://cloudless.gr
# Portfolio: https://baltzakisthemis.com
# WhatsApp: https://wa.me/306977777838
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

SKILL_DIR="$ROOT/.devin/skills/social-accounts-manager/scripts"

echo "=========================================="
echo "  Adding professional links to all accounts"
echo "=========================================="

# 1. Facebook Page
echo ""
echo "--- Facebook Page (cloudless.gr) ---"
bash "$SKILL_DIR/fb-page-update-website.sh "https://cloudless.gr" 2>&1 || echo "  (failed)"

# 2. LinkedIn Organization
echo ""
echo "--- LinkedIn Organization (cloudless.gr) ---"
bash "$SKILL_DIR/li-org-update-website.sh "https://cloudless.gr" 2>&1 || echo "  (failed)"

# 3. LinkedIn Personal
echo ""
echo "--- LinkedIn Personal ---"
bash "$SKILL_DIR/li-personal-update-website.sh "https://cloudless.gr" 2>&1 || echo "  (failed)"

# 4. Facebook Personal
echo ""
echo "--- Facebook Personal ---"
bash "$SKILL_DIR/fb-personal-update-website.sh "https://cloudless.gr" 2>&1 || echo "  (failed)"

# 5. Instagram
echo ""
echo "--- Instagram ---"
bash "$SKILL_DIR/ig-update-url.sh "https://cloudless.gr" 2>&1 || echo "  (failed)"

echo ""
echo "=========================================="
echo "  Done. Run read-all.sh to verify."
echo "=========================================="

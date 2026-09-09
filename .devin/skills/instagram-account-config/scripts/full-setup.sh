#!/usr/bin/env bash
# Full Instagram account setup: login, generate bio, update profile, verify
# Usage: ./full-setup.sh --username t_baltzakis --name "Cloudless" --title "Founder @ " --skills "Cloud Architect · Azure · AWS" --experience "15+ yrs building systems" --location "Athens" --links "cloudless.gr | baltzakisthemis.com" --style bold-brand
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Parse args
USERNAME=""
NAME=""
TITLE="Founder @ "
SKILLS=""
EXPERIENCE=""
LOCATION="Athens"
LINKS=""
STYLE="bold-brand"

while [[ $# -gt 0 ]]; do
    case $1 in
        --username) USERNAME="$2"; shift 2;;
        --name) NAME="$2"; shift 2;;
        --title) TITLE="$2"; shift 2;;
        --skills) SKILLS="$2"; shift 2;;
        --experience) EXPERIENCE="$2"; shift 2;;
        --location) LOCATION="$2"; shift 2;;
        --links) LINKS="$2"; shift 2;;
        --style) STYLE="$2"; shift 2;;
        *) echo "Unknown arg: $1"; exit 1;;
    esac
done

echo "========================================"
echo "  Instagram Full Account Setup"
echo "========================================"
echo "  Username: $USERNAME"
echo "  Name: $NAME"
echo "  Style: $STYLE"
echo "========================================"
echo ""

# Step 1: Check session
echo "Step 1: Checking browser session..."
"$SCRIPT_DIR/check-session.sh"

# Step 2: Generate bio
echo ""
echo "Step 2: Generating stylish bio..."
BIO=$(docker compose exec -T social-api python /app/app/scripts/instagram_bio_generator.py \
    --name "$NAME" \
    --title "$TITLE" \
    --skills "$SKILLS" \
    --experience "$EXPERIENCE" \
    --location "$LOCATION" \
    --links "$LINKS" \
    --style "$STYLE")

echo "Generated bio:"
echo "$BIO"
echo ""

# Step 3: Update profile
echo "Step 3: Updating Instagram profile..."
docker compose exec -T social-api python /app/app/scripts/instagram_profile_update.py \
    --bio "$BIO"

# Step 4: Verify
echo ""
echo "Step 4: Verifying..."
sleep 5
docker compose exec -T social-api python /app/app/scripts/instagram_profile_update.py \
    --verify-bio "$NAME"

# Step 5: Settings checklist
echo ""
echo "Step 5: Settings checklist..."
docker compose exec -T social-api python /app/app/scripts/instagram_settings_checklist.py --list

echo ""
echo "========================================"
echo "  Setup complete!"
echo "========================================"
echo "  Bio updated with style: $STYLE"
echo "  Review the settings checklist above"
echo "  and apply CRITICAL/HIGH items via VNC."
echo "========================================"

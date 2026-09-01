#!/usr/bin/env bash
# Generate the LinkedIn API business use case description from the codebase.
# Outputs a formatted description of how SocialAuto uses the LinkedIn API.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
source .env 2>/dev/null

echo "=== LinkedIn API Business Use Case ==="
echo ""

# Check which scopes are configured
echo "--- Configured LinkedIn OAuth Scopes ---"
grep -A 10 "^LINKEDIN_SCOPES" social-automation/backend/app/api/auth.py | head -10
echo ""

# Check LinkedIn API client methods
echo "--- LinkedIn API Client Methods ---"
grep -n "async def \|def " social-automation/backend/app/services/linkedin_api.py | grep -v "__" | head -20
echo ""

# Check LinkedIn API endpoints
echo "--- LinkedIn API Endpoints ---"
grep -n "@router\.\(get\|post\|put\|delete\)" social-automation/backend/app/api/linkedin.py | head -15
echo ""

echo "--- LinkedIn Products Used ---"
cat << 'EOF'
1. Share on LinkedIn (w_member_social)
   - Create organic posts on personal profiles
   - Create multi-image posts
   - Create article posts
   - Delete posts

2. Community Management API (w_organization_social, r_organization_social)
   - Create organic posts on Company Pages
   - Read post analytics (impressions, clicks, engagement)
   - Read organization stats (follower counts, lifetime analytics)

3. Organizations API (r_organization_admin)
   - Discover Company Pages the member administers
   - Read organization profile data

NOT USED:
- No LinkedIn Ads (rw_ads, r_ads)
- No Campaign Manager integration
- No sponsored content creation
EOF

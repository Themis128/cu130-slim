#!/usr/bin/env bash
# human-gates.sh — surfaces every pending HUMAN-ACTION item in one report.
set -u
cd "$(dirname "$0")/../../../.."
echo "=== HUMAN GATES — $(date '+%H:%M %Z') ==="

# 1. LinkedIn sidecar session — needs manual noVNC login when dead
S=$(docker compose exec -T social-api python -c "
import asyncio
from app.services.linkedin_sidecar import LinkedInSidecarClient
async def m():
    h = await LinkedInSidecarClient().health()
    print(h.get('status','?'), h.get('has_session'))
asyncio.run(m())" 2>/dev/null | tail -1)
echo "1. LinkedIn session: $S"
case "$S" in *True*) ;; *) echo "   → open http://localhost:6080, log in manually";; esac

# 2. X/Twitter OAuth — refresh token dead → re-auth needed
echo "2. X/Twitter: $(docker logs --since=24h social-worker-publishing 2>&1 | grep -c 'oauth2/token') refresh-400s in 24h → re-auth at social.cloudless.gr"

# 3. EspoCRM validation state
echo "3. EspoCRM: $(docker logs --since=24h social-api 2>&1 | grep -c validationFailure) validation 400s in 24h"

# 4. k3s access — #2286 merge state is the only path
echo "4. k3s: PR #2286 $(cd ../cloudless.gr 2>/dev/null && gh pr view 2286 --json state --jq .state 2>/dev/null || echo '?')"

# 5. Secrets rotation
echo "5. ROTATE: LinkedIn password + li_at cookie appeared in session logs — reset + re-login sidecar"

# 6. Stale Metabase BI queries
echo "6. Metabase: stale saved queries reference user.role/.shared/.nodesAccess — fix in Metabase"

# 7. LinkedIn Community Management API for org posting
echo "7. Dev portal: request 'Community Management API' on a dedicated app → w_organization_social"

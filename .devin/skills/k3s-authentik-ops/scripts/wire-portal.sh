#!/bin/bash
set -euo pipefail
. /home/tbaltzakis/.config/secrets/oidc-cloudless-portal
E=${1:-/home/tbaltzakis/cloudless.gr/.env.local}
grep -q "^AUTH_OIDC_" "$E" 2>/dev/null || printf "AUTH_OIDC_ISSUER=%s
AUTH_OIDC_CLIENT_ID=%s
AUTH_OIDC_CLIENT_SECRET=%s
" "$issuer" "$client_id" "$client_secret" >> "$E"
echo "portal env done"

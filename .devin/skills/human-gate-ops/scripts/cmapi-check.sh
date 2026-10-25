#!/usr/bin/env bash
# cmapi-check.sh — did the Community Management API get approved?
# Probe the org social-actions endpoint; 200 = w_organization_social live.
set -euo pipefail
ENV="${ENV:-$(dirname "$0")/../../../../.env}"
TOKEN=$(grep -E '^LINKEDIN_ACCESS_TOKEN=' "$ENV" 2>/dev/null | cut -d= -f2- || true)
ORG="9c4451bb-e820-489f-8676-76ddbc788ffe"
[ -z "$TOKEN" ] && { echo "no LINKEDIN_ACCESS_TOKEN in .env"; exit 1; }
CODE=$(curl -s -o /dev/null -w '%{http_code}' "https://api.linkedin.com/v2/organizationAcls?q=roleAssignee&oauth2_access_token=$TOKEN" || true)
echo "organizationAcls → HTTP $CODE"
[ "$CODE" = 200 ] && echo "org admin scope live" || echo "not yet approved (403/401 expected pre-grant)"

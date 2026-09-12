#!/usr/bin/env bash
# Retrieve Instagram credentials from SocialAuto's secret store.
# Writes username to stdout and password to a temp file (path printed to stderr).
#
# Usage:
#   get-credentials.sh <account_id>
set -euo pipefail

ACCOUNT_ID="${1:-}"
if [[ -z "$ACCOUNT_ID" ]]; then
  echo "Usage: $0 <account_id>" >&2
  exit 1
fi

cd /home/tbaltzakis/cu130-slim
set +u; source .env 2>/dev/null || true; set -u

TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  2>/dev/null | python3 -c "import sys,json; print(json.loads(sys.stdin.read()).get('access_token',''))" 2>/dev/null)

if [[ -z "$TOKEN" ]]; then
  echo "❌ Could not authenticate" >&2
  exit 1
fi

# Get the account info to find the username and type
ACCOUNT_INFO=$(curl -s "http://localhost:8083/api/v1/accounts" \
  -H "Authorization: Bearer $TOKEN" 2>/dev/null)

# Determine which Instagram account this is (business vs personal)
ACCOUNT_TYPE=$(echo "$ACCOUNT_INFO" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
accounts = d if isinstance(d, list) else d.get('accounts', d.get('data', []))
for a in accounts:
    if a['id'] == '$ACCOUNT_ID':
        print(a.get('account_type', ''))
        break
" 2>/dev/null)

# Determine the secret key names based on account type
if [[ "$ACCOUNT_TYPE" == "personal" ]]; then
  USERNAME_KEY="INSTAGRAM_USERNAME_T_BALTZAKIS"
else
  USERNAME_KEY="INSTAGRAM_USERNAME"
fi

# Get the username from the secret store
USERNAME=$(curl -s "http://localhost:8083/api/v1/secrets/${USERNAME_KEY}" \
  -H "Authorization: Bearer $TOKEN" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('value', ''))
except:
    print('')
" 2>/dev/null)

# Fall back to the account's display_name/username
if [[ -z "$USERNAME" ]]; then
  USERNAME=$(echo "$ACCOUNT_INFO" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
accounts = d if isinstance(d, list) else d.get('accounts', d.get('data', []))
for a in accounts:
    if a['id'] == '$ACCOUNT_ID':
        print(a.get('username', a.get('display_name', '')))
        break
" 2>/dev/null)
fi

# Get the password from the secret store (key: INSTAGRAM_PASSWORD)
PASSWORD=$(curl -s "http://localhost:8083/api/v1/secrets/INSTAGRAM_PASSWORD" \
  -H "Authorization: Bearer $TOKEN" 2>/dev/null | python3 -c "
import sys, json
try:
    d = json.loads(sys.stdin.read())
    print(d.get('value', ''))
except:
    print('')
" 2>/dev/null)

# Fall back to .env
if [[ -z "$PASSWORD" ]]; then
  PASSWORD="${INSTAGRAM_PASSWORD:-}"
fi

if [[ -z "$USERNAME" ]]; then
  echo "❌ Could not find username for account $ACCOUNT_ID" >&2
  exit 1
fi

if [[ -z "$PASSWORD" ]]; then
  echo "❌ Could not retrieve password from secret store or .env" >&2
  echo "Store it via: curl -X POST http://localhost:8083/api/v1/secrets/INSTAGRAM_PASSWORD -H 'Authorization: Bearer <token>' -H 'Content-Type: application/json' -d '{\"value\": \"<password>\"}'" >&2
  exit 1
fi

# Write password to a temp file
PASS_FILE=$(mktemp /tmp/ig-pass-XXXXXX)
echo -n "$PASSWORD" > "$PASS_FILE"
chmod 600 "$PASS_FILE"

echo "$USERNAME"
echo "Password written to: $PASS_FILE" >&2
echo "$PASS_FILE" >&2

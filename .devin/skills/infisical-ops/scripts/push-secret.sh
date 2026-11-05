#!/bin/bash
# push-secret.sh NAME -   (value from stdin or arg2 file; masks output)
set -euo pipefail
NAME=$1; SRC=${2:--}
CREDS=/home/tbaltzakis/.config/secrets/infisical
CID=$(grep ^INFISICAL_UA_CLIENT_ID= $CREDS | cut -d= -f2)
CS=$(grep ^INFISICAL_UA_CLIENT_SECRET= $CREDS | cut -d= -f2)
BASE=http://localhost:8090
WS=27660119-de58-4d8b-adaf-a39c8fc8c79e
TOK=$(curl -sk -m 15 -X POST $BASE/api/v1/auth/universal-auth/login -H "Content-Type: application/json" -d "{\"clientId\":\"$CID\",\"clientSecret\":\"$CS\"}" | python3 -c "import json,sys;print(json.load(sys.stdin)[\"accessToken\"])")
[ "$SRC" = "-" ] && VAL=$(cat) || VAL=$(cat "$SRC")
BODY=$(python3 -c "import json,os;print(json.dumps({\"workspaceId\":\"$WS\",\"environment\":\"prod\",\"secretValue\":os.environ[\"V\"],\"secretPath\":\"/\",\"type\":\"shared\"}))" V="$VAL" 2>/dev/null || true)
[ -z "$BODY" ] && BODY=$(V="$VAL" python3 -c "import json,os;print(json.dumps({\"workspaceId\":\"$WS\",\"environment\":\"prod\",\"secretValue\":os.environ[\"V\"],\"secretPath\":\"/\",\"type\":\"shared\"}))")
CODE=$(curl -sk -m 15 -o /dev/null -w %{http_code} -X POST "$BASE/api/v3/secrets/raw/$NAME" -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d "$BODY")
if [ "$CODE" = "400" ]; then CODE=$(curl -sk -m 15 -o /dev/null -w %{http_code} -X PATCH "$BASE/api/v3/secrets/raw/$NAME" -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" -d "$BODY"); fi
echo "$NAME -> $CODE"

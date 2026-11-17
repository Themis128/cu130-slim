#!/bin/bash
# slack-mcp-remote.sh <socialauto|cloudless> [extra mcp-remote args]
# Launches mcp-remote -> https://mcp.slack.com/mcp for one workspace using that
# workspace's own "MCP Workspace Reader" Slack app (client_id+secret read from
# the Windows-side clients.json at runtime — secrets never enter this repo,
# mcp_config.json, or argv beyond the proxy's own --static-oauth-client-info).
#
# Why per-app client_ids: mcp.slack.com OAuth validates redirect_uri against
# the Slack app's registered redirect URLs. Slack's first-party Claude client
# only accepts claude.ai callbacks; each workspace app has localhost callbacks
# registered via apps.manifest.* (see C:\Users\tbaltzakis\.zcode\tools\slack-mcp).
set -euo pipefail

LABEL="${1:?usage: slack-mcp-remote.sh <socialauto|cloudless>}"
shift || true

CLIENTS=/mnt/c/Users/tbaltzakis/.zcode/tools/slack-mcp/clients.json
[[ -f "$CLIENTS" ]] || { echo "clients.json not found: $CLIENTS" >&2; exit 1; }

read -r CLIENT_ID CLIENT_SECRET <<<"$(python3 - "$CLIENTS" "$LABEL" <<'PY'
import json, sys
c = json.load(open(sys.argv[1]))[sys.argv[2]]
print(c["client_id"], c["client_secret"])
PY
)"

if [[ "$LABEL" == "socialauto" ]]; then
  PORT=3118
else
  PORT=3119
fi

NODE=/home/tbaltzakis/.local/bin/node
[[ -x "$NODE" ]] || NODE=node
PROXY=/mnt/c/Users/tbaltzakis/.local/lib/mcp-remote/node_modules/mcp-remote/dist/proxy.js
[[ -f "$PROXY" ]] || PROXY="$HOME/.local/lib/mcp-remote/node_modules/mcp-remote/dist/proxy.js"

INFO="$(python3 - "$CLIENT_ID" "$CLIENT_SECRET" "$PORT" <<'PY'
import json, sys
cid, sec, port = sys.argv[1], sys.argv[2], sys.argv[3]
print(json.dumps({
  "client_id": cid,
  "client_secret": sec,
  "redirect_uris": [f"http://localhost:{port}/callback",
                    f"http://127.0.0.1:{port}/callback"],
}))
PY
)"

exec "$NODE" "$PROXY" \
  "https://mcp.slack.com/mcp" \
  "$PORT" --host localhost --callback-path /callback \
  --static-oauth-client-info "$INFO" \
  "$@"

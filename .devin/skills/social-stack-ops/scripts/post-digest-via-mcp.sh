#!/usr/bin/env bash
# Build SocialAuto daily digest inside social-api and print markdown for Slack MCP.
# Does not post — agent posts via plugin-slack-slack slack_send_message to C0BT263L17U.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
OUT="${1:-$ROOT/.tmp-socialauto-digest-slack.md}"

docker cp "$ROOT/.devin/skills/social-stack-ops/scripts/build_digest_for_slack.py" social-api:/tmp/build_digest_for_slack.py
docker exec social-api python /tmp/build_digest_for_slack.py
docker cp social-api:/tmp/socialauto-digest-slack.md "$OUT"
echo "Wrote $OUT — post contents with Slack MCP slack_send_message channel_id=C0BT263L17U"

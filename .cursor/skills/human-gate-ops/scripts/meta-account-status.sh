#!/usr/bin/env bash
# Read Meta/FB sidecar session state and report to the ops channel.
# Read-only watch — the restriction clear + portfolio connect is owner-side.
set -u
cd /home/tbaltzakis/cu130-slim
STATE=$(curl -fs --max-time 15 http://localhost:9226/session/validate 2>/dev/null \
  || curl -fs --max-time 15 http://localhost:9226/session 2>/dev/null \
  || echo '{"error":"sidecar-unreachable-or-asleep"}')
docker compose exec -T social-api python - "$STATE" <<'PYEOF'
import asyncio, sys, json
async def main():
    from app.core.config import get_settings
    from app.services.slack_notifications import _post_slack_text, _get_slack_token
    try:
        d = json.loads(sys.argv[1])
        state = d.get("status") or ("logged_in" if d.get("logged_in") else d.get("error") or "unknown")
    except Exception:
        state = sys.argv[1][:80]
    s = get_settings()
    ok, err, _ = await _post_slack_text(
        text=f":mag: *Meta account-status check* — `{state}` (#785)\nWhen clear: Accounts Center → connect cloudless.gr portfolio → leads_retrieval App Review",
        webhook_url="", token=_get_slack_token(), channel_id=s.SLACK_CHANNEL_ID,
        purpose="meta-status")
    print("posted" if ok else f"failed: {err}", "| state:", state)
asyncio.run(main())
PYEOF

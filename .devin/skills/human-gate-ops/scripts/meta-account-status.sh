#!/usr/bin/env bash
# Read Meta Accounts Center status via the headed browser profile and
# report to the ops channel. Read-only — the restriction clear/connect is
# the owner's action; this just watches + surfaces.
set -u
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - <<'PYEOF' 2>&1 | tail -15
import asyncio, json
async def main():
    from app.services.browser_orchestrator import get_browser_bridge
    from app.services.slack_notifications import _post_slack_text, _get_slack_token
    from app.core.config import get_settings
    try:
        b = get_browser_bridge()
        out = await b.check_session()
        state = "restricted/unknown" if not out else ("logged-in" if out.get("logged_in") else "not-logged-in")
    except Exception as e:
        state = f"check failed: {e.__class__.__name__}"
    s = get_settings()
    await _post_slack_text(
        text=f":mag: *Meta account-status check* — {state} (#785)\nNext when clear: Accounts Center → connect cloudless.gr portfolio → leads_retrieval App Review",
        webhook_url="", token=_get_slack_token(), channel_id=s.SLACK_CHANNEL_ID, purpose="meta-status")
    print("posted:", state)
asyncio.run(main())
PYEOF

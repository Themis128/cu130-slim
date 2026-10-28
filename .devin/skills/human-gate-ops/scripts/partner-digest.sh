#!/usr/bin/env bash
# Pull the EspoCRM "Partners & influencers" TargetList and post it as a
# Slack digest for owner approve/drop (#793). Read-only.
set -u
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - <<'PYEOF' 2>&1 | tail -20
import asyncio
async def main():
    from app.services import espocrm_client as espo
    from app.services.slack_notifications import _post_slack_text, _get_slack_token
    from app.core.config import get_settings
    lists = await espo.list_all("TargetList", {"type":"equals","attribute":"name","value":"Partners & influencers"}) if hasattr(espo, "list_all") else []
    if not lists:
        print("no TargetList found — pull via API manually"); return
    tl = lists[0]
    items = await espo.list_related("TargetList", tl.get("id"), "targets") if hasattr(espo, "list_related") else []
    names = [t.get("name") or t.get("emailAddress") or t.get("id") for t in items]
    s = get_settings()
    body = (":handshake: *Partner shortlist pending approval (#793)*\n" +
            "\n".join(f"• {n}" for n in names[:15]) +
            f"\nApprove or drop in EspoCRM TargetList `{tl.get('name')}` or reply here.")
    await _post_slack_text(text=body, webhook_url="", token=_get_slack_token(),
                           channel_id=s.SLACK_CHANNEL_ID, purpose="partner-digest")
    print(f"posted {len(names)} names")
asyncio.run(main())
PYEOF

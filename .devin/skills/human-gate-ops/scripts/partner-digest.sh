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
    tl_id = await espo.target_list_id("Partners & influencers")
    if not tl_id:
        print("no TargetList 'Partners & influencers' — check name"); return
    tl = {"id": tl_id, "name": "Partners & influencers"}
    import json as _j
    resp = await espo.espo_get("/Lead", params={"searchParams": _j.dumps({
        "maxSize": 50, "select": ["name", "emailAddress"],
        "where": [{"type": "linkedWith", "attribute": "targetLists", "value": [tl_id]}],
    })})
    items = resp.get("list", [])
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

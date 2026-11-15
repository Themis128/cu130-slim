"""Ensure LinkedIn Conversions API rules exist for every configured event.

For each event in ``linkedin_advanced.CONVERSION_EVENTS``: resolve the rule
URN (env → social_secrets); when missing, look the rule up by name on the ads
account and create it via ``POST /rest/conversions`` if absent, then persist
the ``urn:lla:llaPartnerConversion:{id}`` back into ``social_secrets`` under
the event's env-var key. Idempotent — safe on a daily beat.
"""

import logging

from celery import shared_task

from app.worker._async import run_async

logger = logging.getLogger(__name__)


async def _ensure() -> dict:
    from app.services import linkedin_advanced
    from app.services.secret_store import secret_store
    from app.services.slack_notifications import post_alert_to_slack

    ctx = await linkedin_advanced._ctx()
    if not ctx:
        return {"skip": "linkedin-advanced-disabled"}

    existing = await linkedin_advanced.list_conversion_rules()
    if existing.get("status") not in (None, 200):
        await post_alert_to_slack(f":warning: LinkedIn conversion ensure: list rules → {existing.get('status')}")
        return {"error": existing.get("status")}
    rules = existing.get("rules") or []
    by_name = {r.get("name"): r for r in rules}

    out = {"created": [], "found": [], "errors": []}
    for event, (env_key, conv_type) in linkedin_advanced.CONVERSION_EVENTS.items():
        urn = await linkedin_advanced.resolve_conversion_rule(event)
        if urn:
            out["found"].append(event)
            continue
        # Rule name = bare event name — matches the audit task's lookup table
        # and any pre-existing rules created under the same convention.
        name = event
        rule = by_name.get(name) or by_name.get(f"SocialAuto {event.replace('_', ' ').title()}")
        if rule:
            rule_id = rule.get("id")
            if rule_id:
                new_urn = f"urn:lla:llaPartnerConversion:{rule_id}"
                await secret_store.set(env_key, new_urn, f"LinkedIn conversion rule for {event}")
                out["found"].append(event)
                continue
        created = await linkedin_advanced.create_conversion_rule(name, conv_type)
        if created.get("rule_urn"):
            await secret_store.set(env_key, created["rule_urn"], f"LinkedIn conversion rule for {event}")
            out["created"].append({"event": event, "rule": created["rule_urn"]})
        else:
            out["errors"].append({"event": event, "error": created})

    if out["errors"]:
        await post_alert_to_slack(":warning: LinkedIn conversion ensure errors:\n" + "\n".join(str(e) for e in out["errors"]))
    return out


@shared_task(name="app.worker.tasks.linkedin_conversion_ensure.ensure_linkedin_conversion_rules", bind=True, max_retries=0)
def ensure_linkedin_conversion_rules(self):
    return run_async(_ensure())

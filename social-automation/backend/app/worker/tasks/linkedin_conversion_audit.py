"""Daily audit of the LinkedIn Conversions API lane.

Verifies the ``audit_booked`` + ``lead_created`` conversion rules exist,
are enabled, use ``CONVERSIONS_API`` method, and that the rule ids match
the ``LINKEDIN_CONVERSION_RULE*`` env URNs the code streams to. Alerts
#ops on any drift — a silently-dead conversions lane means lost
post→click→booking attribution.
"""

import os

from app.worker._async import run_async
from app.worker.celery_app import celery_app

REST = "https://api.linkedin.com/rest"
HDRS = {"Linkedin-Version": "202610", "X-Restli-Protocol-Version": "2.0.0"}
AD_ACCOUNT = "urn:li:sponsoredAccount:512642510"

_RULE_ENVS = {
    "audit_booked": "LINKEDIN_CONVERSION_RULE",
    "lead_created": "LINKEDIN_CONVERSION_RULE_LEAD_CREATED",
}


async def _audit() -> dict:
    import httpx

    from app.services import linkedin_advanced
    from app.services.slack_notifications import post_alert_to_slack

    ctx = await linkedin_advanced._ctx()
    if not ctx:
        return {"skip": "linkedin-advanced-disabled"}
    token, _ = ctx

    async with httpx.AsyncClient(timeout=30, headers={"Authorization": f"Bearer {token}", **HDRS}) as c:
        r = await c.get(f"{REST}/conversions", params={"q": "account", "account": AD_ACCOUNT, "count": 100})
    if r.status_code != 200:
        await post_alert_to_slack(f":warning: LinkedIn conversion audit: GET /conversions → {r.status_code}")
        return {"error": r.status_code}

    rules = {e.get("name"): e for e in r.json().get("elements", [])}
    problems: list[str] = []
    for name, env in _RULE_ENVS.items():
        rule = rules.get(name)
        urn = os.environ.get(env, "")
        want_id = urn.rsplit(":", 1)[-1] if urn else ""
        if not rule:
            problems.append(f"{name}: rule missing — recreate via POST /rest/conversions")
            continue
        if not rule.get("enabled"):
            problems.append(f"{name}: disabled")
        if rule.get("conversionMethod") != "CONVERSIONS_API":
            problems.append(f"{name}: method={rule.get('conversionMethod')} (want CONVERSIONS_API)")
        if want_id and str(rule.get("id")) != want_id:
            problems.append(f"{name}: env URN id {want_id} != live id {rule.get('id')} — fix {env}")

    if problems:
        await post_alert_to_slack(":warning: LinkedIn conversion audit:\n" + "\n".join(problems))
    return {"ok": not problems, "problems": problems, "rules": sorted(rules)}


@celery_app.task(name="app.worker.tasks.linkedin_conversion_audit.audit_linkedin_conversions")
def audit_linkedin_conversions() -> dict:
    return run_async(_audit())

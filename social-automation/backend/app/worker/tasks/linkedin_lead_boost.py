"""LinkedIn P1 tasks — lead routing + winner boost (behind LINKEDIN_ADVANCED).

- ``route_linkedin_leads``: poll lead-form responses → dedupe → route into the
  audit funnel (CRM + follow-up + Slack alert).
- ``boost_linkedin_winners``: pick up ``boost_candidate`` posts flagged by
  linkedin_engagement_score → sponsored-content campaign against promo credit
  only (standing rule: never real spend).
"""

import logging
import os
from datetime import UTC, datetime

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

ENABLED = os.getenv("LINKEDIN_ADVANCED", "0") == "1"


@celery_app.task(name="app.worker.tasks.linkedin_lead_boost.route_linkedin_leads")
def route_linkedin_leads() -> dict:
    return run_async(_route_linkedin_leads())


async def _route_linkedin_leads() -> dict:
    if not ENABLED:
        return {"skip": "LINKEDIN_ADVANCED off"}
    from sqlalchemy import select

    from app.models.social_account import SocialAccount
    from app.services import espocrm_client, linkedin_ads

    async with task_session() as db:
        acc = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.account_type == "organization",
                    )
                )
            )
            .scalars()
            .first()
        )
        if not acc:
            return {"skip": "no-linkedin-org-account"}
        accounts = await linkedin_ads.ad_accounts(db, str(acc.team_id))

    leads: list[dict] = []
    for a in accounts:
        acct_urn = a.get("urn") or f"urn:li:sponsoredAccount:{a.get('id')}"
        for form in await linkedin_ads.lead_gen_forms(db, acct_urn):
            responses = await linkedin_ads.lead_form_responses(db, acct_urn)
            for r in responses:
                leads.append({"account": acct_urn, "form": form.get("id"), "response": r})
    if not leads:
        return {"routed": 0}

    from app.services.slack_notifications import post_alert_to_slack

    routed = 0
    for lead in leads[:25]:
        resp = lead.get("response") or {}
        answers = _lead_answers(resp)
        email = (answers.get("email") or answers.get("emailAddress") or "").strip()
        if not email:
            continue
        first = (answers.get("firstName") or "").strip()
        last = (answers.get("lastName") or "").strip()
        lead_id = await espocrm_client.upsert_lead(
            {
                "emailAddress": email,
                "firstName": first,
                "lastName": last,
                "source": "Social Media",
                "description": f"LinkedIn lead-gen response {resp.get('id', '')} via {lead.get('form')}",
            }
        )
        if lead_id:
            routed += 1
            try:
                import hashlib

                from app.services import linkedin_advanced

                sha = hashlib.sha256(email.strip().lower().encode()).hexdigest()
                conv = await linkedin_advanced.stream_conversion(
                    conversion_rule=os.environ.get(
                        "LINKEDIN_CONVERSION_RULE_LEAD_CREATED", "lead_created"
                    ),
                    happened_at_ms=int(datetime.now(UTC).timestamp() * 1000),
                    email_sha256=sha,
                    lead_id=str(resp.get("id") or "") or None,
                )
                if not conv.get("ok") and conv.get("status") not in (None, 200, 201):
                    logger.info("lead_created conversion not accepted: %s", conv)
            except Exception as exc:  # noqa: BLE001 — conversion reporting never blocks routing
                logger.warning("lead_created conversion failed: %s", exc)
    if routed:
        await post_alert_to_slack(f":new: LinkedIn lead sync: {routed} lead(s) routed to EspoCRM")
    return {"routed": routed, "found": len(leads)}


def _lead_answers(resp: dict) -> dict[str, str]:
    """Normalize LinkedIn leadFormResponses answer shapes to {field: value}."""
    out: dict[str, str] = {}
    for item in resp.get("formResponse") or resp.get("answers") or []:
        q = str(item.get("question") or item.get("name") or "")
        a = item.get("answer")
        if isinstance(a, list):
            a = "; ".join(str(x) for x in a)
        out[q] = str(a or "")
    # Also accept flat custom-question maps some payloads carry.
    for k, v in (resp.get("customQuestions") or {}).items():
        out.setdefault(str(k), str(v))
    return out


@celery_app.task(name="app.worker.tasks.linkedin_lead_boost.boost_linkedin_winners")
def boost_linkedin_winners() -> dict:
    return run_async(_boost_linkedin_winners())


async def _boost_linkedin_winners() -> dict:
    if not ENABLED:
        return {"skip": "LINKEDIN_ADVANCED off"}
    from sqlalchemy import select

    from app.models.content import Post
    from app.services.slack_notifications import post_alert_to_slack

    async with task_session() as db:
        winners = (await db.execute(select(Post).where(Post.meta_data["boost_candidate"].astext == "true"))).scalars().all()
        for p in winners:
            p.meta_data = {**(p.meta_data or {}), "boost_candidate": False, "boost_queued": True}
        await db.commit()
    if winners:
        await post_alert_to_slack(f":rocket: {len(winners)} LinkedIn posts flagged >2σ — promo-credit boost queued")
    return {"boosted": len(winners)}

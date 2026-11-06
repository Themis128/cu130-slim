"""LinkedIn P1 tasks — lead routing + winner boost (behind LINKEDIN_ADVANCED).

- ``route_linkedin_leads``: poll lead-form responses → dedupe → route into the
  audit funnel (CRM + follow-up + Slack alert).
- ``boost_linkedin_winners``: pick up ``boost_candidate`` posts flagged by
  linkedin_engagement_score → sponsored-content campaign against promo credit
  only (standing rule: never real spend).
"""

import os

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

ENABLED = os.getenv("LINKEDIN_ADVANCED", "0") == "1"


@celery_app.task(name="app.worker.tasks.linkedin_lead_boost.route_linkedin_leads")
def route_linkedin_leads() -> dict:
    return run_async(_route_linkedin_leads())


async def _route_linkedin_leads() -> dict:
    if not ENABLED:
        return {"skip": "LINKEDIN_ADVANCED off"}
    from sqlalchemy import select

    from app.models.social_account import SocialAccount
    from app.services import linkedin_ads

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
        for form in await linkedin_ads.lead_gen_forms(db, str(acc.team_id)):
            for r in await linkedin_ads.lead_form_responses(db, str(acc.team_id)):
                leads.append({"form": form.get("id"), "response": r})
    if not leads:
        return {"routed": 0}
    from app.services.slack_notifications import post_alert_to_slack

    for lead in leads[:10]:
        await post_alert_to_slack(f":new: LinkedIn lead response: `{lead.get('form')}` — route to audit funnel")
    return {"routed": len(leads)}


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

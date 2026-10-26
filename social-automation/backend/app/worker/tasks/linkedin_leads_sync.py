"""LinkedIn lead-gen → CRM. Pulls form responses → Lead rows (dedup by URN)."""
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.linkedin_leads_sync.sync_linkedin_leads",
             bind=True, max_retries=0)
def sync_linkedin_leads(self):
    from app.worker._async import run_async
    return run_async(_run())


async def _run():
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.lead import Lead, LeadSource
    from app.models.social_account import SocialAccount
    from app.services import linkedin_ads

    new = 0
    async with async_session_maker() as db:
        acc = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform == "linkedin"))).scalars().first()
        accs = await linkedin_ads.ad_accounts(db)
        for a in accs:
            urn = a.get("urn") or f"urn:li:sponsoredAccount:{a.get('id')}"
            forms = await linkedin_ads.lead_gen_forms(db, urn.split(":")[-1])
            for f in forms:
                lead_urn = f.get("id") or ""
                if not lead_urn:
                    continue
                if (await db.execute(select(Lead).where(
                        Lead.meta_data["linkedin_form_id"].astext == lead_urn
                ))).scalars().first():
                    continue
                resp = f.get("leadResponse", {}) or {}
                fname = (resp.get("firstName") or "").strip()
                lname = (resp.get("lastName") or "").strip()
                db.add(Lead(
                    team_id=acc.team_id if acc else None,
                    source=LeadSource.linkedin_ads,
                    social_account_id=acc.id if acc else None,
                    name=(f"{fname} {lname}".strip() or "LinkedIn Lead")[:200],
                    email=resp.get("email", "")[:320],
                    company_size=None,
                    interest=None,
                    notes=f"LinkedIn lead-gen form {lead_urn}",
                    meta_data={"linkedin_form_id": lead_urn,
                               "form_name": f.get("name", "")[:80]}))
                new += 1
        await db.commit()
    logger.info("[li-leads] %d new leads", new)
    return {"new_leads": new}

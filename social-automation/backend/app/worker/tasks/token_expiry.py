"""Token expiry sweep — proactive refresh-ahead with reauth escalation.

Scans SocialAccount meta ``expires_at``/``token_expires_at``, buckets
entries inside per-provider lead times into warn (refresh lane covers it)
vs reauth (expired on a non-refreshable provider → Slack escalation).

Same logic as ``scripts/token_expiry_sweep.py`` — inlined so it runs
in-container without the script file (``/app`` is read-only).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_LEAD: dict[str, timedelta] = {
    "tiktok": timedelta(hours=4),
    "instagram": timedelta(days=10),
    "facebook": timedelta(days=14),
    "meta": timedelta(days=14),
    "linkedin": timedelta(days=7),
    "twitter": timedelta(days=7),
    "x": timedelta(days=7),
    "threads": timedelta(days=10),
}

_NON_REFRESHABLE = {"linkedin", "x", "twitter", "whatsapp"}


_REAUTH_DEDUP = timedelta(hours=24)


async def _sweep() -> dict:
    from app.models import SocialAccount  # noqa: PLC0415

    now = datetime.now(UTC)
    report: dict[str, list] = {"warn": [], "reauth": []}
    async with task_session() as db:
        rows = (await db.execute(select(SocialAccount).where(SocialAccount.status.in_(["active", "connected", "expired", "revoked"])))).scalars().all()
        dirty = False
        for acc in rows:
            plat = (acc.platform or "").lower()
            md = dict(acc.meta_data or {})
            raw = md.get("expires_at") or md.get("token_expires_at")
            col_exp = getattr(acc, "token_expires_at", None)
            if not raw and col_exp:
                raw = col_exp.isoformat()
            exp = None
            if raw:
                try:
                    exp = datetime.fromisoformat(str(raw))
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=UTC)
                except ValueError:
                    exp = None
            dead = (getattr(acc, "status", None) or "active") in ("expired", "revoked")
            lead = _LEAD.get(plat)
            if plat in _NON_REFRESHABLE:
                if dead or (exp and exp <= now):
                    pass  # reauth candidate below
                elif lead and exp and exp <= now + lead:
                    report["warn"].append(
                        {
                            "platform": plat,
                            "account": acc.username or str(acc.id),
                            "expires": exp.isoformat(),
                        }
                    )
                    continue
                else:
                    continue
                last = md.get("reauth_escalated_at")
                if last:
                    try:
                        if now - datetime.fromisoformat(str(last)) < _REAUTH_DEDUP:
                            continue
                    except ValueError:
                        pass
                md["reauth_escalated_at"] = now.isoformat()
                acc.meta_data = md
                dirty = True
                report["reauth"].append(
                    {
                        "platform": plat,
                        "account": acc.username or str(acc.id),
                        "expires": exp.isoformat() if exp else "n/a",
                    }
                )
                continue
            if lead is None:
                continue
            inside = dead or (exp is not None and exp <= now + lead)
            if not inside:
                continue
            if dead:
                # Chronic dead-token warning — dedupe to once/day like reauth.
                last = md.get("reauth_escalated_at")
                if last:
                    try:
                        if now - datetime.fromisoformat(str(last)) < _REAUTH_DEDUP:
                            continue
                    except ValueError:
                        pass
                md["reauth_escalated_at"] = now.isoformat()
                acc.meta_data = md
                dirty = True
            report["warn"].append(
                {
                    "platform": plat,
                    "account": acc.username or str(acc.id),
                    "expires": exp.isoformat() if exp else "n/a",
                }
            )
        if dirty:
            await db.commit()
    return report


async def _post(report: dict) -> None:
    if not (report["warn"] or report["reauth"]):
        return
    lines = [":key: *Token expiry sweep*"]
    for e in report["warn"]:
        lines.append(f"• {e['platform']}/{e['account']} expires {e['expires'][:16]} — refresh lane should cover it")
    for e in report["reauth"]:
        lines.append(f":rotating_light: {e['platform']}/{e['account']} EXPIRED — needs human reauth (consent lane)")
    try:
        from app.services.slack_notifications import post_alert_to_slack  # noqa: PLC0415

        await post_alert_to_slack("\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.exception("token expiry slack post failed")


@celery_app.task(name="app.worker.tasks.token_expiry.run_token_expiry_sweep", bind=True, max_retries=0)
def run_token_expiry_sweep(self) -> dict:
    report = run_async(_sweep())
    run_async(_post(report))
    logger.info("token expiry sweep: %s", report)
    return {"status": "ok", **{k: len(v) for k, v in report.items()}}

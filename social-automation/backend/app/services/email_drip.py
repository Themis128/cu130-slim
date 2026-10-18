"""Autonomous email drip engine — first-approach + renewal loops.

Turns the two branded template campaigns into self-running loops:

- **first_approach** — every EspoCRM lead that has never received the drip
  gets the first-approach campaign. New inbound leads are caught on the next
  daily sweep; the backlog is mailed once, then never again.
- **renewal** — EspoCRM Closed-Won opportunities whose ``closeDate``
  anniversary falls inside the lookahead window, and contacts reaching a
  ``createdAt`` anniversary, get the renewal campaign — once per annual
  cycle, re-arming every year.

Design rules:

- Template campaigns are **draft** ``EmailCampaign`` rows marked
  ``meta_data.drip_template = "<key>"`` — they are never sent directly.
  Each wave clones the template into a sendable campaign tagged
  ``meta_data.drip_key`` (+ ``drip_cycle`` for renewal).
- The campaign-level content-similarity gate (``find_duplicate_campaign``)
  intentionally refuses identical sends; drips legitimately resend the same
  copy to *different* people, so it bypasses clones carrying ``drip_key``.
  The real invariant — **never mail the same address the same drip/cycle** —
  is enforced here per-recipient against ``email_recipients``.
- Failed recipients do not count as mailed (they never received the email)
  and are eligible next wave; suppression is re-checked per send, and the
  daily send budget paces every wave.
- Every wave writes a CRM note on each mailed entity (audit trail inside
  EspoCRM) and reports the wave to Slack when it actually mails or errors.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.core.config import get_settings
from app.models.email_campaign import (
    CampaignKind,
    EmailCampaign,
    EmailRecipient,
    RecipientStatus,
)
from app.models.user import Team
from app.services import espocrm_client
from app.services.leads import _is_synthetic_email, is_valid_email

logger = logging.getLogger(__name__)

DRIP_FIRST_APPROACH = "first_approach"
DRIP_RENEWAL = "renewal"

# Same test-probe markers as crm_hygiene — a CI lead must never get mail even
# if it slips past quarantine between sweeps.
_TEST_MARKERS = ("ci-test+", "@example.invalid", "@example.com", "test@test")


def email_drip_enabled() -> bool:
    return get_settings().EMAIL_DRIP_ENABLED and espocrm_client.configured()


def _norm_email(record: dict[str, Any]) -> str:
    return str(record.get("emailAddress") or "").strip().lower()


def _is_test_address(email: str, name: str | None = None) -> bool:
    return any(m in email for m in _TEST_MARKERS) or "ci functional test" in (
        name or ""
    ).lower()


def _mailable(email: str, name: str | None = None) -> bool:
    """Candidate-stage gate — mirrors what resolve_audience will enforce at
    queue time (valid + non-synthetic) and adds the internal exclusion: the
    owner's mailbox and our own sending domain must never get marketing
    drips, or the loop mails us to ourselves."""
    if not email or not is_valid_email(email) or _is_synthetic_email(email):
        return False
    if _is_test_address(email, name):
        return False
    settings = get_settings()
    internal = {settings.DIGEST_EMAIL_TO.strip().lower()}
    domain = settings.SMTP_FROM.rsplit("@", 1)[-1].strip().lower()
    return email not in internal and not email.endswith(f"@{domain}")


def _parse_dt(raw: Any) -> datetime | None:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        # EspoCRM date-only fields ("2026-10-18") parse cleanly via date().
        try:
            from datetime import date

            d = date.fromisoformat(str(raw)[:10])
            return datetime(d.year, d.month, d.day, tzinfo=UTC)
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _already_dripped(db, team_id: uuid.UUID, key: str, cycle: str | None) -> set[str]:
    """Emails that already received (or are queued for) this drip/cycle."""
    q = (
        select(EmailRecipient.email)
        .join(EmailCampaign, EmailRecipient.campaign_id == EmailCampaign.id)
        .where(
            EmailRecipient.team_id == team_id,
            EmailRecipient.status.in_([RecipientStatus.pending, RecipientStatus.sent]),
            EmailCampaign.meta_data["drip_key"].astext == key,
        )
    )
    if cycle is not None:
        q = q.where(EmailCampaign.meta_data["drip_cycle"].astext == cycle)
    return {str(e).strip().lower() for (e,) in (await db.execute(q)).all()}


async def _template(db, team_id: uuid.UUID, key: str) -> EmailCampaign | None:
    return (
        await db.execute(
            select(EmailCampaign)
            .where(
                EmailCampaign.team_id == team_id,
                EmailCampaign.meta_data["drip_template"].astext == key,
            )
            .order_by(EmailCampaign.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()


def _clone(template: EmailCampaign, key: str, wave: str, cycle: str | None) -> EmailCampaign:
    meta = {k: v for k, v in (template.meta_data or {}).items() if k != "drip_template"}
    meta.update(
        {
            "drip_key": key,
            "template_id": str(template.id),
            "wave": wave,
        }
    )
    if cycle is not None:
        meta["drip_cycle"] = cycle
    return EmailCampaign(
        id=uuid.uuid4(),
        team_id=template.team_id,
        name=f"{template.name} — {wave}",
        kind=template.kind or CampaignKind.marketing,
        subject=template.subject,
        text_body=template.text_body,
        html_body=template.html_body,
        from_name=template.from_name,
        reply_to=template.reply_to,
        survey_url=template.survey_url,
        meta_data=meta,
    )


async def _first_approach_candidates() -> list[dict[str, Any]]:
    out = []
    for lead in await espocrm_client.list_leads_for_campaign():
        email = _norm_email(lead)
        name = " ".join(
            p for p in (lead.get("firstName"), lead.get("lastName")) if p
        ) or lead.get("accountName")
        if not _mailable(email, name):
            continue
        out.append(
            {
                "email": email,
                "name": name,
                "crm_entity": "Lead",
                "crm_id": lead.get("id"),
                "cycle": None,
            }
        )
    return out


def _anniversary(start: datetime, now: datetime) -> datetime | None:
    """The next whole-year anniversary of ``start`` at/after today.

    Returns ``None`` when the date can't anniversarize (Feb 29 on a non-leap
    target year falls back to Feb 28 — treated as the anniversary).
    """
    years = max(1, now.year - start.year)
    try:
        candidate = start.replace(year=start.year + years)
    except ValueError:
        candidate = start.replace(year=start.year + years, day=28)
    if candidate < now:
        years += 1
        try:
            candidate = start.replace(year=start.year + years)
        except ValueError:
            candidate = start.replace(year=start.year + years, day=28)
    return candidate


async def _renewal_candidates(now: datetime) -> list[dict[str, Any]]:
    """Entities entering a renewal window inside the lookahead.

    Two real signals: Closed-Won opportunities reaching a closeDate
    anniversary (resolved to contact emails, then account email), and
    contacts reaching a createdAt anniversary (first year with us → first
    renewal conversation). Cycle = the anniversary's calendar year, so the
    loop re-arms each year without re-mailing inside one.
    """
    settings = get_settings()
    lookahead = timedelta(days=settings.EMAIL_DRIP_RENEWAL_LOOKAHEAD_DAYS)
    out: list[dict[str, Any]] = []

    contacts = await espocrm_client.list_contacts()
    contact_by_id = {str(c.get("id")): c for c in contacts}
    contact_emails = {_norm_email(c) for c in contacts}

    for contact in contacts:
        created = _parse_dt(contact.get("createdAt"))
        if not created:
            continue
        anniv = _anniversary(created, now)
        if anniv is None or not (now <= anniv <= now + lookahead):
            continue
        email = _norm_email(contact)
        name = " ".join(
            p for p in (contact.get("firstName"), contact.get("lastName")) if p
        ) or contact.get("accountName")
        if not _mailable(email, name):
            continue
        out.append(
            {
                "email": email,
                "name": name,
                "crm_entity": "Contact",
                "crm_id": contact.get("id"),
                "cycle": str(anniv.year),
            }
        )

    try:
        opportunities = await espocrm_client.list_all(
            "Opportunity",
            select=["id", "name", "stage", "closeDate", "accountId", "contactsIds"],
        )
    except Exception:  # noqa: BLE001 — Opportunity entity absent shouldn't kill the drip
        logger.exception("email drip: opportunity pull failed")
        opportunities = []

    account_by_id: dict[str, dict[str, Any]] = {}
    if opportunities:
        try:
            for acc in await espocrm_client.list_accounts():
                account_by_id[str(acc.get("id"))] = acc
        except Exception:  # noqa: BLE001
            logger.exception("email drip: account pull failed")

    seen = {c["email"] for c in out}
    for opp in opportunities:
        if "won" not in str(opp.get("stage") or "").lower():
            continue
        close = _parse_dt(opp.get("closeDate"))
        if not close:
            continue
        anniv = _anniversary(close, now)
        if anniv is None or not (now <= anniv <= now + lookahead):
            continue
        # Resolve recipient: linked contacts first, account email fallback.
        for cid in opp.get("contactsIds") or []:
            contact = contact_by_id.get(str(cid))
            if not contact:
                continue
            email = _norm_email(contact)
            if email and email not in seen:
                seen.add(email)
                name = " ".join(
                    p
                    for p in (contact.get("firstName"), contact.get("lastName"))
                    if p
                ) or contact.get("accountName")
                if _mailable(email, name):
                    out.append(
                        {
                            "email": email,
                            "name": name,
                            "crm_entity": "Contact",
                            "crm_id": contact.get("id"),
                            "cycle": str(anniv.year),
                        }
                    )
        acc = account_by_id.get(str(opp.get("accountId") or ""))
        acc_email = _norm_email(acc or {})
        if (
            acc_email
            and acc_email not in seen
            and acc_email not in contact_emails
            and _mailable(acc_email, (acc or {}).get("name"))
        ):
            seen.add(acc_email)
            out.append(
                {
                    "email": acc_email,
                    "name": (acc or {}).get("name"),
                    "crm_entity": "Account",
                    "crm_id": (acc or {}).get("id"),
                    "cycle": str(anniv.year),
                }
            )
    return out


async def _run_drip(
    db,
    team_id: uuid.UUID,
    key: str,
    candidates: list[dict[str, Any]],
    *,
    dry_run: bool,
    now: datetime,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "drip": key, "candidates": len(candidates), "mailed": 0,
        "already_sent": 0, "campaign_id": None, "errors": [],
    }
    if not candidates:
        return stats

    template = await _template(db, team_id, key)
    if template is None:
        stats["errors"].append("no drip_template campaign found")
        logger.warning("email drip %s: no template campaign for team %s", key, team_id)
        return stats

    by_cycle: dict[str | None, list[dict[str, Any]]] = {}
    for c in candidates:
        by_cycle.setdefault(c.get("cycle"), []).append(c)

    from app.services.email_campaigns import resolve_audience

    for cycle, group in by_cycle.items():
        mailed = await _already_dripped(db, team_id, key, cycle)
        fresh = [c for c in group if c["email"] not in mailed]
        stats["already_sent"] += len(group) - len(fresh)
        if not fresh:
            continue

        wave = now.strftime("%Y-%m-%d") + (f" cycle-{cycle}" if cycle else "")
        if dry_run:
            stats.setdefault("would_mail", []).extend(
                {"email": c["email"], "cycle": cycle} for c in fresh
            )
            continue

        clone = _clone(template, key, wave, cycle)
        db.add(clone)
        await db.commit()
        recipients = await resolve_audience(
            db,
            clone,
            explicit_emails=[{"email": c["email"], "name": c["name"]} for c in fresh],
        )
        queued = [r for r in recipients if r.status == RecipientStatus.pending]
        if not queued:
            # Every fresh address landed on suppression — nothing to send.
            await db.delete(clone)
            await db.commit()
            stats["suppressed_only"] = stats.get("suppressed_only", 0) + len(fresh)
            continue

        from app.worker.celery_app import celery_app

        celery_app.send_task(
            "app.worker.tasks.email_campaigns.send_campaign", args=[str(clone.id)]
        )
        stats["mailed"] += len(queued)
        stats["campaign_id"] = str(clone.id)

        by_email = {c["email"]: c for c in fresh}
        for rec in queued:
            cand = by_email.get(rec.email)
            if not cand or not cand.get("crm_id"):
                continue
            try:
                await espocrm_client.add_lead_note(
                    str(cand["crm_id"]),
                    f"Email drip '{key}' queued: campaign '{clone.name}'",
                    entity=str(cand.get("crm_entity") or "Lead"),
                )
            except Exception:  # noqa: BLE001 — audit note is best-effort
                logger.exception("email drip: CRM note failed for %s", rec.email)
    return stats


async def run_email_drip_sweep(*, dry_run: bool = False) -> dict[str, Any]:
    """Sweep all teams: first-approach (daily) + renewal (cycle-windowed)."""
    from app.db.session import async_session_maker

    now = datetime.now(UTC)
    report: dict[str, Any] = {"dry_run": dry_run, "teams": [], "mailed": 0}
    async with async_session_maker() as db:
        teams = (await db.execute(select(Team))).scalars().all()
        for team in teams:
            team_stats: dict[str, Any] = {"team_id": str(team.id)}
            for key, candidates in [
                (DRIP_FIRST_APPROACH, await _first_approach_candidates()),
                (DRIP_RENEWAL, await _renewal_candidates(now)),
            ]:
                try:
                    s = await _run_drip(
                        db, team.id, key, candidates, dry_run=dry_run, now=now
                    )
                except Exception as exc:  # noqa: BLE001 — one drip must not kill the other
                    logger.exception("email drip %s failed for team %s", key, team.id)
                    s = {"drip": key, "errors": [str(exc)]}
                team_stats[key] = s
                report["mailed"] += s.get("mailed", 0)
            report["teams"].append(team_stats)

    if report["mailed"] or any(
        s.get("errors") for t in report["teams"] for s in t.values() if isinstance(s, dict)
    ):
        await _slack_report(report)
    return report


async def _slack_report(report: dict[str, Any]) -> None:
    try:
        from app.services.slack_notifications import post_digest_text_to_slack

        lines = ["*Email drip sweep*"]
        for t in report["teams"]:
            for key in (DRIP_FIRST_APPROACH, DRIP_RENEWAL):
                s = t.get(key) or {}
                if not s:
                    continue
                lines.append(
                    f"  {key}: {s.get('mailed', 0)} mailed"
                    f" · {s.get('candidates', 0)} candidates"
                    f" · {s.get('already_sent', 0)} already mailed"
                    + (f" · errors: {'; '.join(s['errors'])}" if s.get("errors") else "")
                )
        await post_digest_text_to_slack("\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.exception("email drip: slack report failed")

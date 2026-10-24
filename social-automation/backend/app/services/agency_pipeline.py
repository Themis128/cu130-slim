"""Autonomous-agency pipeline — Front Desk → Architect → Proposal → PM.

Four bounded agents (per agent-workforce / agentic-bpm contracts):

- **triage_sweep** (Front Desk): classifies new leads against the service
  catalog, extracts an auditable domain, queues an audit row, and stamps
  an explainable ``Agency triage:`` note on the EspoCRM lead.
- **audit_sweep** (Architect): runs the deterministic site audit for
  pending ``agency_audits`` rows and writes a summary note back to the CRM.
- **proposal_sweep** (Sales drafter): turns a completed audit into a priced,
  fixed-scope proposal — always a *draft* gated on human approval
  (``AGENCY_PROPOSAL_AUTOSEND`` stays False; the approve lane is Slack +
  the admin API).
- **pm_sweep** (Supervisor): drives won projects through the 14-day
  milestone plan, drafting the client update at each checkpoint and
  compiling the handoff package on day 14.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.agency import (
    AgencyAudit,
    AgencyAuditStatus,
    AgencyProject,
    AgencyProjectStatus,
    AgencyProposal,
    AgencyProposalStatus,
)
from app.models.lead import Lead
from app.models.user import Team
from app.services import espocrm_client as espo
from app.services.agency_catalog import (
    AUDIT_CTA_URL,
    BUNDLE_NAME,
    BUNDLE_PRICE_EUR,
    DELIVERY_DAYS,
    SERVICES,
    classify_services,
    extract_domain,
    price_range,
)
from app.services.site_audit import run_site_audit
from app.services.url_safety import UnsafeUrlError


def _factory(custom=None):
    if custom is not None:
        return custom
    from app.worker._async import get_session_factory

    return get_session_factory()

logger = logging.getLogger(__name__)

TRIAGE_MARKER = "Agency triage:"
AUDIT_MARKER = "Site audit:"
_TRIAGE_TEXT_LIMIT = 4000

# The 14-day fixed-scope delivery plan (cloudless.gr promise).
MILESTONES: tuple[tuple[int, str, str], ...] = (
    (1, "kickoff", "Project kickoff — repo/env access confirmed, scope locked"),
    (5, "architecture", "Architecture draft ready for review"),
    (10, "dashboards", "Dashboards & tracking wired"),
    (14, "handoff", "Handoff package compiled and sent"),
)
_OVERDUE_GRACE = timedelta(days=2)


async def _default_team_id(db: AsyncSession) -> uuid.UUID | None:
    """The operator's team — agency rows attach to the first team."""
    row = (await db.execute(select(Team.id).order_by(Team.created_at.asc()).limit(1))).first()
    return row[0] if row else None


def _lead_blob(lead: Lead | dict[str, Any]) -> str:
    """All free text available for classification."""
    if isinstance(lead, Lead):
        parts = [lead.name, lead.notes or "", (lead.meta_data or {}).get("message", "")]
    else:
        parts = [lead.get("name") or "", lead.get("description") or ""]
    return "\n".join(p for p in parts if p)[:_TRIAGE_TEXT_LIMIT]


def _services_line(services: list[str]) -> str:
    names = [SERVICES[s].name for s in services if s in SERVICES]
    if len(names) == len(SERVICES):
        return BUNDLE_NAME
    return ", ".join(names) if names else "unclassified"


# ---------------------------------------------------------------------------
# Front Desk — triage sweep
# ---------------------------------------------------------------------------


async def _triage_local_leads(db: AsyncSession, team_id: uuid.UUID) -> dict[str, int]:
    """Local ``leads`` rows that have not been triaged yet."""
    rows = (
        await db.execute(
            select(Lead).where(Lead.team_id == team_id).order_by(Lead.created_at.desc()).limit(200)
        )
    ).scalars().all()
    stats = {"triaged": 0, "audits": 0, "skipped": 0}
    for lead in rows:
        md = dict(lead.meta_data or {})
        if md.get("agency_triaged"):
            continue
        blob = _lead_blob(lead)
        ranked = classify_services(blob)
        services = [slug for slug, _score, _hits in ranked]
        domain = extract_domain(
            str((lead.meta_data or {}).get("website") or ""),
            lead.notes or "",
            lead.email.split("@")[-1] if "@" in lead.email else "",
        )
        md["agency_triaged"] = {
            "services": services,
            "domain": domain,
            "matches": {slug: hits for slug, _s, hits in ranked},
            "triaged_at": datetime.now(UTC).isoformat(),
        }
        lead.meta_data = md
        stats["triaged"] += 1

        if domain:
            exists = (
                await db.execute(
                    select(AgencyAudit.id).where(
                        AgencyAudit.team_id == team_id,
                        AgencyAudit.url.contains(domain),
                    ).limit(1)
                )
            ).first()
            if not exists:
                db.add(AgencyAudit(team_id=team_id, lead_id=lead.id, url=f"https://{domain}"))
                stats["audits"] += 1

        espo_id = md.get("espocrm_lead_id")
        if espo_id:
            hits = "; ".join(f"{s} ({', '.join(h[:3])})" for s, _sc, h in ranked[:4])
            await espo.add_lead_note(
                str(espo_id),
                f"{TRIAGE_MARKER} services={_services_line(services)}; domain={domain or 'n/a'}; signals: {hits or 'none'}",
            )
    return stats


async def _triage_espo_leads(db: AsyncSession, team_id: uuid.UUID) -> dict[str, int]:
    """EspoCRM-native leads (contact-form path) — note marker is the dedupe."""
    stats = {"triaged": 0, "audits": 0, "skipped": 0}
    leads = await espo.list_all(
        "Lead",
        select=["id", "name", "emailAddress", "website", "description", "status", "createdAt"],
        where=[
            {"type": "notEquals", "attribute": "status", "value": "Dead"},
            {"type": "notEquals", "attribute": "status", "value": "Converted"},
        ],
        max_items=300,
    )
    for lead in leads:
        lid = lead.get("id") or ""
        if not lid:
            continue
        # Dedupe: triage note already attached, or an audit row already exists.
        exists = (
            await db.execute(
                select(AgencyAudit.id).where(AgencyAudit.espo_lead_id == lid).limit(1)
            )
        ).first()
        if exists:
            stats["skipped"] += 1
            continue
        notes = await espo.list_all(
            "Note",
            select=["id", "post"],
            where=[
                {"type": "equals", "attribute": "parentType", "value": "Lead"},
                {"type": "equals", "attribute": "parentId", "value": lid},
            ],
            max_items=50,
        )
        if any(TRIAGE_MARKER in (n.get("post") or "") for n in notes):
            stats["skipped"] += 1
            continue

        blob = _lead_blob(lead)
        ranked = classify_services(blob)
        services = [slug for slug, _score, _hits in ranked]
        domain = extract_domain(lead.get("website"), lead.get("description"))
        email_domain = (lead.get("emailAddress") or "").split("@")[-1]
        if not domain and email_domain and "." in email_domain:
            domain = email_domain if email_domain not in {
                "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "icloud.com",
                "proton.me", "aol.com", "gmx.com",
            } else None

        hits = "; ".join(f"{s} ({', '.join(h[:3])})" for s, _sc, h in ranked[:4])
        note = f"{TRIAGE_MARKER} services={_services_line(services)}; domain={domain or 'n/a'}; signals: {hits or 'none'}"
        if not await espo.add_lead_note(lid, note):
            stats["skipped"] += 1
            continue
        stats["triaged"] += 1

        if domain:
            # One audit per domain — many leads can share a site; don't
            # queue a duplicate row per lead.
            dup = (
                await db.execute(
                    select(AgencyAudit.id).where(
                        AgencyAudit.team_id == team_id,
                        AgencyAudit.url.contains(domain),
                    ).limit(1)
                )
            ).first()
            if not dup:
                db.add(AgencyAudit(
                    team_id=team_id, espo_lead_id=lid, url=f"https://{domain}",
                    meta_data={"lead_name": lead.get("name"), "lead_email": lead.get("emailAddress")},
                ))
                stats["audits"] += 1
    return stats


async def triage_sweep(*, session_factory=None) -> dict[str, Any]:
    """Front Desk agent: classify untriaged leads, queue site audits."""
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    factory = _factory(session_factory)
    async with factory() as db:
        team_id = await _default_team_id(db)
        if not team_id:
            return {"status": "skipped", "reason": "no_team"}
        local = await _triage_local_leads(db, team_id)
        espo_stats = (
            await _triage_espo_leads(db, team_id)
            if espo.configured()
            else {"triaged": 0, "audits": 0, "skipped": 0}
        )
        await db.commit()
    stats = {"status": "ok", "local": local, "espo": espo_stats}
    logger.info("agency triage sweep: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Architect — audit sweep
# ---------------------------------------------------------------------------


async def audit_sweep(*, session_factory=None) -> dict[str, Any]:
    """Run pending site audits (bounded per run)."""
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    limit = max(1, int(get_settings().AGENCY_AUDIT_MAX_PER_RUN))
    factory = _factory(session_factory)
    stats = {"status": "ok", "completed": 0, "failed": 0}
    async with factory() as db:
        rows = (
            await db.execute(
                select(AgencyAudit)
                .where(AgencyAudit.status == AgencyAuditStatus.pending)
                .order_by(AgencyAudit.created_at.asc())
                .limit(limit)
            )
        ).scalars().all()
        for audit in rows:
            audit.status = AgencyAuditStatus.running
            await db.flush()
            try:
                result = await run_site_audit(audit.url)
            except UnsafeUrlError as exc:
                audit.status = AgencyAuditStatus.failed
                audit.error = f"unsafe_url: {exc}"
                stats["failed"] += 1
                continue
            except Exception as exc:  # noqa: BLE001 — one bad site must not stall the batch
                audit.status = AgencyAuditStatus.failed
                audit.error = str(exc)[:1000]
                stats["failed"] += 1
                logger.warning("agency audit failed for %s: %r", audit.url, exc)
                continue
            audit.status = AgencyAuditStatus.completed
            audit.score = result["score"]
            audit.findings = result["findings"]
            audit.recommended_services = result["recommended_services"]
            audit.action_plan = result["action_plan"]
            audit.completed_at = datetime.now(UTC)
            audit.error = None
            md = dict(audit.meta_data or {})
            md.update({
                "ttfb_ms": result["ttfb_ms"],
                "http_status": result["http_status"],
                "tech": result["tech"],
                "analytics": result["analytics"],
            })
            audit.meta_data = md
            stats["completed"] += 1

            if audit.espo_lead_id:
                top = [f["finding"] for f in result["findings"] if f["severity"] in ("critical", "warning")][:5]
                await espo.add_lead_note(
                    audit.espo_lead_id,
                    f"{AUDIT_MARKER} {result['url']} — score {result['score']}/100, "
                    f"TTFB {result['ttfb_ms']}ms, stack: {', '.join(result['tech'][:4]) or 'unknown'}\n"
                    + ("\n".join(f"- {t}" for t in top) if top else "- No blocking issues found"),
                )
        await db.commit()
    logger.info("agency audit sweep: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Sales drafter — proposal sweep
# ---------------------------------------------------------------------------


def render_proposal(*, audit: AgencyAudit, recipient_name: str | None, services: list[str]) -> tuple[str, str]:
    """Deterministic fixed-scope proposal (markdown). Returns (subject, body)."""
    name = (recipient_name or "").strip() or "there"
    site = audit.url
    price_low, price_high = price_range(services)
    bundle = len(services) == len(SERVICES)

    lines = [
        f"Hi {name},",
        "",
        f"thanks for reaching out — I ran a first-pass audit of {site} ahead of our call.",
        "",
        f"**Site health score: {audit.score}/100**" if audit.score is not None else "**Audit complete**",
        "",
        "What I found:",
        (audit.action_plan or "- Baseline looks healthy"),
        "",
        "Recommended scope (fixed-price, delivered in 14 days):",
    ]
    for slug in services:
        svc = SERVICES.get(slug)
        if svc:
            lines.append(f"- **{svc.name}** — €{svc.price_low_eur}–€{svc.price_high_eur}: {svc.summary}")
    if bundle:
        lines.append(f"- **{BUNDLE_NAME}** — €{BUNDLE_PRICE_EUR}/month flat")
    elif price_low:
        lines.append(f"\nEstimated total: **€{price_low}–€{price_high}**, one fixed scope.")
    lines += [
        "",
        "How the 14 days run:",
        "- Day 1 — kickoff, scope locked, access confirmed",
        "- Day 5 — architecture draft for your review",
        "- Day 10 — dashboards & tracking wired",
        "- Day 14 — handoff package: your code, your docs, yours to keep",
        "",
        f"Want to talk it through? Grab a free 30-minute call: {AUDIT_CTA_URL}",
        "",
        "— Themistoklis, Cloudless",
    ]
    subject = f"Your {site} audit + a fixed-scope plan"
    return subject, "\n".join(lines)


async def _post_proposal_alert(proposal: AgencyProposal, audit: AgencyAudit | None) -> None:
    try:
        from app.services.slack_notifications import _button, post_alert_to_slack

        who = proposal.recipient_name or proposal.recipient_email or "lead"
        url = audit.url if audit else "n/a"
        price = (
            f"€{proposal.price_low_eur}–€{proposal.price_high_eur}"
            if proposal.price_low_eur and proposal.price_high_eur
            else "n/a"
        )
        text = f":memo: *Proposal drafted* — {who} ({url}), {price}, {len(proposal.services)} services"
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "actions",
                "elements": [
                    _button("Approve & send", "socialauto_proposal_approve",
                            value=str(proposal.id), style="primary",
                            confirm="Email this proposal to the lead?"),
                    _button("Reject", "socialauto_proposal_reject",
                            value=str(proposal.id), style="danger"),
                    _button("Review in admin", "open_socialauto_admin",
                            url="https://social.cloudless.gr/admin"),
                ],
            },
        ]
        await post_alert_to_slack(text, blocks=blocks)
    except Exception:  # noqa: BLE001
        logger.exception("proposal slack alert failed")


async def proposal_sweep(*, session_factory=None) -> dict[str, Any]:
    """Draft proposals for completed audits that have none."""
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    factory = _factory(session_factory)
    stats = {"status": "ok", "drafted": 0}
    async with factory() as db:
        audits = (
            await db.execute(
                select(AgencyAudit)
                .where(AgencyAudit.status == AgencyAuditStatus.completed)
                .order_by(AgencyAudit.created_at.asc())
                .limit(50)
            )
        ).scalars().all()
        for audit in audits:
            has_proposal = (
                await db.execute(
                    select(AgencyProposal.id).where(AgencyProposal.audit_id == audit.id).limit(1)
                )
            ).first()
            if has_proposal:
                continue
            services = [s for s in (audit.recommended_services or []) if s in SERVICES]
            if not services:
                services = [s for s, _sc, _h in classify_services(audit.action_plan or "")]
            if not services:
                services = ["cloud"]
            md = dict(audit.meta_data or {})
            recipient_email: str | None = md.get("lead_email")
            recipient_name: str | None = md.get("lead_name")
            if audit.lead_id:
                lead = (
                    await db.execute(select(Lead).where(Lead.id == audit.lead_id).limit(1))
                ).scalar_one_or_none()
                if lead:
                    recipient_email = recipient_email or lead.email
                    recipient_name = recipient_name or lead.name

            subject, body = render_proposal(audit=audit, recipient_name=recipient_name, services=services)
            low, high = price_range(services)
            proposal = AgencyProposal(
                team_id=audit.team_id,
                audit_id=audit.id,
                lead_id=audit.lead_id,
                espo_lead_id=audit.espo_lead_id,
                recipient_email=recipient_email,
                recipient_name=recipient_name,
                services=services,
                price_low_eur=low or None,
                price_high_eur=high or None,
                subject=subject,
                body_md=body,
                status=AgencyProposalStatus.draft,
            )
            db.add(proposal)
            await db.flush()
            stats["drafted"] += 1
            await _post_proposal_alert(proposal, audit)
        await db.commit()
    logger.info("agency proposal sweep: %s", stats)
    return stats


async def send_proposal(db: AsyncSession, proposal: AgencyProposal, *, approver: str = "admin") -> dict[str, Any]:
    """Human-approved send — the only path that emails a lead a proposal."""
    if proposal.status not in (AgencyProposalStatus.draft, AgencyProposalStatus.approved):
        return {"ok": False, "error": f"proposal is {proposal.status}, not draft/approved"}
    if not proposal.recipient_email:
        return {"ok": False, "error": "no recipient email on proposal"}
    from app.services.email_digest import send_email

    await send_email(
        subject=proposal.subject,
        text_body=proposal.body_md,
        to_addrs=[proposal.recipient_email],
        reply_to="tbaltzakis@cloudless.gr",
        from_name="Themistoklis — Cloudless",
    )
    proposal.status = AgencyProposalStatus.sent
    proposal.sent_at = datetime.now(UTC)
    proposal.approved_at = proposal.approved_at or datetime.now(UTC)
    md = dict(proposal.meta_data or {})
    md["approved_by"] = approver
    proposal.meta_data = md
    await db.commit()
    if proposal.espo_lead_id:
        await espo.add_lead_note(
            proposal.espo_lead_id,
            f"Proposal sent ({len(proposal.services)} services, "
            f"€{proposal.price_low_eur}–€{proposal.price_high_eur})",
        )
    return {"ok": True, "sent_to": proposal.recipient_email}


# ---------------------------------------------------------------------------
# Supervisor — 14-day PM sweep
# ---------------------------------------------------------------------------


def project_milestones() -> list[dict[str, Any]]:
    return [{"day": d, "key": k, "label": lbl, "done": False, "done_at": None}
            for d, k, lbl in MILESTONES]


def _days_in(project: AgencyProject, now: datetime) -> int:
    kickoff = project.kickoff_at
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=UTC)
    return max(0, (now - kickoff).days + 1)


def _draft_update(project: AgencyProject, milestone: dict[str, Any]) -> str:
    return (
        f"Day {milestone['day']} update — {project.name}\n\n"
        f"Milestone reached: {milestone['label']}.\n"
        f"Next checkpoint: day {min(milestone['day'] + 5, DELIVERY_DAYS)} of {DELIVERY_DAYS}. "
        "Everything remains on the fixed scope agreed at kickoff."
    )


def _render_handoff(project: AgencyProject) -> str:
    done = [m["label"] for m in (project.milestones or []) if m.get("done")]
    return (
        f"# Handoff — {project.name}\n\n"
        f"Kickoff: {project.kickoff_at:%Y-%m-%d} · Delivered: {datetime.now(UTC):%Y-%m-%d}\n\n"
        "## Milestones completed\n"
        + "\n".join(f"- {m}" for m in done)
        + "\n\n## What you own\n- Full source — your repository\n"
          "- Infrastructure definitions and deploy scripts\n"
          "- This documentation set\n\n"
          "*Generated by the Cloudless delivery pipeline.*"
    )


async def pm_sweep(*, session_factory=None) -> dict[str, Any]:
    """Supervisor agent: milestone updates + day-14 handoff + overdue flags."""
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    factory = _factory(session_factory)
    now = datetime.now(UTC)
    stats = {"status": "ok", "updates": 0, "delivered": 0, "overdue": 0}
    drafts: list[str] = []
    async with factory() as db:
        projects = (
            await db.execute(
                select(AgencyProject).where(AgencyProject.status == AgencyProjectStatus.active)
            )
        ).scalars().all()
        for project in projects:
            day = _days_in(project, now)
            milestones = [dict(m) for m in (project.milestones or project_milestones())]
            changed = False
            for m in milestones:
                if m.get("done") or day < int(m.get("day", 0)):
                    continue
                m["done"] = True
                m["done_at"] = now.isoformat()
                changed = True
                draft = _draft_update(project, m)
                drafts.append(draft)
                stats["updates"] += 1
                md = dict(project.meta_data or {})
                md.setdefault("client_updates", []).append(
                    {"day": day, "milestone": m["key"], "draft": draft, "drafted_at": now.isoformat()}
                )
                project.meta_data = md
                project.last_client_update_at = now
            if changed:
                project.milestones = milestones

            if day >= DELIVERY_DAYS and not project.handoff_md:
                project.handoff_md = _render_handoff(project)
                project.status = AgencyProjectStatus.delivered
                stats["delivered"] += 1

            due = project.due_at if project.due_at.tzinfo else project.due_at.replace(tzinfo=UTC)
            if now > due + _OVERDUE_GRACE and project.status == AgencyProjectStatus.active:
                project.status = AgencyProjectStatus.stalled
                stats["overdue"] += 1
        await db.commit()

    if drafts or stats["delivered"] or stats["overdue"]:
        try:
            from app.services.slack_notifications import post_alert_to_slack

            lines = [":calendar: *Agency PM sweep*"]
            lines += [f"```{d}```" for d in drafts[:5]]
            if stats["delivered"]:
                lines.append(f"{stats['delivered']} project(s) hit day 14 — handoff compiled")
            if stats["overdue"]:
                lines.append(f":warning: {stats['overdue']} project(s) past due — needs a human")
            await post_alert_to_slack("\n".join(lines))
        except Exception:  # noqa: BLE001
            logger.exception("pm sweep slack report failed")
    logger.info("agency pm sweep: %s", stats)
    return stats

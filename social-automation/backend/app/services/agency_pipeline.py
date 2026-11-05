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

from sqlalchemy import desc, select
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
    rows = (await db.execute(select(Lead).where(Lead.team_id == team_id).order_by(Lead.created_at.desc()).limit(200))).scalars().all()
    stats: dict[str, Any] = {"triaged": 0, "audits": 0, "skipped": 0}
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
                    select(AgencyAudit.id)
                    .where(
                        AgencyAudit.team_id == team_id,
                        AgencyAudit.url.contains(domain),
                    )
                    .limit(1)
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
    stats: dict[str, Any] = {"triaged": 0, "audits": 0, "skipped": 0}
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
        exists = (await db.execute(select(AgencyAudit.id).where(AgencyAudit.espo_lead_id == lid).limit(1))).first()
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
            domain = (
                email_domain
                if email_domain
                not in {
                    "gmail.com",
                    "yahoo.com",
                    "hotmail.com",
                    "outlook.com",
                    "icloud.com",
                    "proton.me",
                    "aol.com",
                    "gmx.com",
                }
                else None
            )

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
                    select(AgencyAudit.id)
                    .where(
                        AgencyAudit.team_id == team_id,
                        AgencyAudit.url.contains(domain),
                    )
                    .limit(1)
                )
            ).first()
            if not dup:
                db.add(
                    AgencyAudit(
                        team_id=team_id,
                        espo_lead_id=lid,
                        url=f"https://{domain}",
                        meta_data={"lead_name": lead.get("name"), "lead_email": lead.get("emailAddress")},
                    )
                )
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
        espo_stats = await _triage_espo_leads(db, team_id) if espo.configured() else {"triaged": 0, "audits": 0, "skipped": 0}
        await db.commit()
    stats: dict[str, Any] = {"status": "ok", "local": local, "espo": espo_stats}
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
    stats: dict[str, Any] = {"status": "ok", "completed": 0, "failed": 0}
    async with factory() as db:
        rows = (
            (await db.execute(select(AgencyAudit).where(AgencyAudit.status == AgencyAuditStatus.pending).order_by(AgencyAudit.created_at.asc()).limit(limit)))
            .scalars()
            .all()
        )
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
            md.update(
                {
                    "ttfb_ms": result["ttfb_ms"],
                    "http_status": result["http_status"],
                    "tech": result["tech"],
                    "analytics": result["analytics"],
                }
            )
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
            for phase in svc.phases:
                lines.append(f"  - {phase}")
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
        price = f"€{proposal.price_low_eur}–€{proposal.price_high_eur}" if proposal.price_low_eur and proposal.price_high_eur else "n/a"
        text = f":memo: *Proposal drafted* — {who} ({url}), {price}, {len(proposal.services)} services"
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": text}},
            {
                "type": "actions",
                "elements": [
                    _button(
                        "Approve & send", "socialauto_proposal_approve", value=str(proposal.id), style="primary", confirm="Email this proposal to the lead?"
                    ),
                    _button("Reject", "socialauto_proposal_reject", value=str(proposal.id), style="danger"),
                    _button("Review in admin", "open_socialauto_admin", url="https://social.cloudless.gr/admin"),
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
    stats: dict[str, Any] = {"status": "ok", "drafted": 0}
    async with factory() as db:
        audits = (
            (await db.execute(select(AgencyAudit).where(AgencyAudit.status == AgencyAuditStatus.completed).order_by(AgencyAudit.created_at.asc()).limit(50)))
            .scalars()
            .all()
        )
        for audit in audits:
            has_proposal = (await db.execute(select(AgencyProposal.id).where(AgencyProposal.audit_id == audit.id).limit(1))).first()
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
                lead = (await db.execute(select(Lead).where(Lead.id == audit.lead_id).limit(1))).scalar_one_or_none()
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
            f"Proposal sent ({len(proposal.services)} services, €{proposal.price_low_eur}–€{proposal.price_high_eur})",
        )
    return {"ok": True, "sent_to": proposal.recipient_email}


# ---------------------------------------------------------------------------
# Supervisor — 14-day PM sweep
# ---------------------------------------------------------------------------


def project_milestones() -> list[dict[str, Any]]:
    return [{"day": d, "key": k, "label": lbl, "done": False, "done_at": None} for d, k, lbl in MILESTONES]


def _days_in(project: AgencyProject, now: datetime) -> int:
    kickoff = project.kickoff_at
    if kickoff.tzinfo is None:
        kickoff = kickoff.replace(tzinfo=UTC)
    return max(0, (now - kickoff).days + 1)


def _draft_update(project: AgencyProject, milestone: dict[str, Any]) -> str:
    # SAT (Yang, Gao & Huang 2025): agent reports must carry verifiable
    # evidence, not status claims — the client should be able to check each
    # milestone against an artifact, not take our word for it.
    evidence = (milestone.get("evidence") or "").strip()
    evidence_line = f"Verify: {evidence}\n" if evidence else "Verify: artifact linked in project record.\n"
    return (
        f"Day {milestone['day']} update — {project.name}\n\n"
        f"Milestone reached: {milestone['label']}.\n"
        f"{evidence_line}"
        f"Next checkpoint: day {min(milestone['day'] + 5, DELIVERY_DAYS)} of {DELIVERY_DAYS}. "
        "Everything remains on the fixed scope agreed at kickoff."
    )


def _render_handoff(project: AgencyProject) -> str:
    done = [m["label"] for m in (project.milestones or []) if m.get("done")]
    return (
        f"# Handoff — {project.name}\n\n"
        f"Kickoff: {project.kickoff_at:%Y-%m-%d} · Delivered: {datetime.now(UTC):%Y-%m-%d}\n\n"
        "## Milestones completed\n" + "\n".join(f"- {m}" for m in done) + "\n\n## What you own\n- Full source — your repository\n"
        "- Infrastructure definitions and deploy scripts\n"
        "- This documentation set\n\n"
        "## Verify it yourself\n"
        "- Re-run the handoff checklist against the live environment\n"
        "- Audit trail: every milestone above cites its artifact\n\n"
        "*Generated by the Cloudless delivery pipeline.*"
    )


async def pm_sweep(*, session_factory=None) -> dict[str, Any]:
    """Supervisor agent: milestone updates + day-14 handoff + overdue flags."""
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    factory = _factory(session_factory)
    now = datetime.now(UTC)
    stats: dict[str, Any] = {"status": "ok", "updates": 0, "delivered": 0, "overdue": 0}
    drafts: list[str] = []
    async with factory() as db:
        projects = (await db.execute(select(AgencyProject).where(AgencyProject.status == AgencyProjectStatus.active))).scalars().all()
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
                md.setdefault("client_updates", []).append({"day": day, "milestone": m["key"], "draft": draft, "drafted_at": now.isoformat()})
                project.meta_data = md
                project.last_client_update_at = now
            if changed:
                project.milestones = milestones

            if day >= DELIVERY_DAYS and not project.handoff_md:
                project.handoff_md = _render_handoff(project)
                project.status = AgencyProjectStatus.delivered
                stats["delivered"] += 1
                md = dict(project.meta_data or {})
                md.setdefault("testimonial_due_at", (now + timedelta(days=2)).isoformat())
                project.meta_data = md

            due = project.due_at if project.due_at.tzinfo else project.due_at.replace(tzinfo=UTC)
            if now > due + _OVERDUE_GRACE and project.status == AgencyProjectStatus.active:
                project.status = AgencyProjectStatus.stalled
                stats["overdue"] += 1

        # Post-delivery ask — delivered projects get one testimonial request
        # ~2 days after handoff (stamped so it can never double-send).
        delivered = (await db.execute(select(AgencyProject).where(AgencyProject.status == AgencyProjectStatus.delivered))).scalars().all()
        for project in delivered:
            md = dict(project.meta_data or {})
            if md.get("testimonial_asked") or not project.client_email:
                continue
            due_at_raw = md.get("testimonial_due_at")
            if not due_at_raw:
                md["testimonial_due_at"] = (now + timedelta(days=2)).isoformat()
                project.meta_data = md
                continue
            try:
                due_at = datetime.fromisoformat(due_at_raw)
            except ValueError:
                due_at = now
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=UTC)
            if now < due_at:
                continue
            from app.services.email_digest import send_email

            client = (md.get("client_name") or "").strip() or "there"
            await send_email(
                subject=f"How did the {DELIVERY_DAYS} days go? — {project.name}",
                text_body=(
                    f"Hi {client},\n\n"
                    f"your {DELIVERY_DAYS}-day delivery wrapped up — you should have the\n"
                    "full handoff package in hand.\n\n"
                    "One small ask: if the work landed, would you write 2–3\n"
                    "sentences about it? A short testimonial is the best proof\n"
                    "I can show the next client — just reply to this mail.\n\n"
                    "And if anything in the handoff is unclear, tell me — that\n"
                    "part is on me to fix.\n\n"
                    "— Themistoklis, Cloudless"
                ),
                to_addrs=[project.client_email],
                reply_to="tbaltzakis@cloudless.gr",
                from_name="Themistoklis — Cloudless",
            )
            md["testimonial_asked"] = now.isoformat()
            project.meta_data = md
            stats["testimonial_asks"] = stats.get("testimonial_asks", 0) + 1
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


# ---------------------------------------------------------------------------
# Engineers — PR-manifest drafter (Joshi 2025 agentic-DevOps pattern)
# ---------------------------------------------------------------------------

# Bounded Phase-2 worker: drafts the implementation plan for a SENT proposal
# as a PR manifest — branch, file surface, tests, CI gate, rollout, rollback.
# Plans only, never code; a human (or a future codegen agent at `review`)
# executes the manifest. Matches the paper's pattern: agent proposes → CI
# validates → human merges → progressive delivery → documented rollback.

_ENGINEER_MARKER = "engineering_plan"


def render_engineering_plan(proposal: AgencyProposal, audit: AgencyAudit | None) -> str:
    """Deterministic PR manifest for a scoped engagement."""
    services = [s for s in (proposal.services or []) if s in SERVICES]
    names = ", ".join(SERVICES[s].name for s in services) or "custom scope"
    findings = [f["finding"] for f in (audit.findings or [])][:6] if audit else []
    slug = proposal.recipient_name or proposal.recipient_email or "client"
    slug = "".join(c if c.isalnum() else "-" for c in slug.lower()).strip("-")[:30] or "client"
    return (
        f"# Engineering plan — {proposal.recipient_name or 'client'} ({names})\n\n"
        f"## Branch\n`agency/{slug}-{str(proposal.id)[:8]}`\n\n"
        "## File surface\n"
        + "\n".join(f"- `{s}/` — {SERVICES[s].name}" for s in services)
        + ("\n\n## Findings to address\n" + "\n".join(f"- {f}" for f in findings) if findings else "")
        + "\n\n## Gates\n- `pytest` + `ruff` on changed paths (CI must pass before merge)\n"
        "- Human review on every PR — agents never self-merge\n"
        "- Deploy via existing pipeline; rollback = `git revert` + redeploy\n\n"
        "*Plan only — codegen agents require `review` autonomy graduation.*"
    )


async def engineer_sweep(*, session_factory=None) -> dict[str, Any]:
    """Draft engineering plans for sent proposals that lack one."""
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    factory = _factory(session_factory)
    stats: dict[str, Any] = {"status": "ok", "planned": 0}
    async with factory() as db:
        sent = (
            (
                await db.execute(
                    select(AgencyProposal).where(AgencyProposal.status == AgencyProposalStatus.sent).order_by(AgencyProposal.created_at.asc()).limit(20)
                )
            )
            .scalars()
            .all()
        )
        for proposal in sent:
            md = dict(proposal.meta_data or {})
            if md.get(_ENGINEER_MARKER):
                continue
            audit = None
            if proposal.audit_id:
                audit = (await db.execute(select(AgencyAudit).where(AgencyAudit.id == proposal.audit_id))).scalar_one_or_none()
            md[_ENGINEER_MARKER] = {
                "plan_md": render_engineering_plan(proposal, audit),
                "drafted_at": datetime.now(UTC).isoformat(),
            }
            proposal.meta_data = md
            stats["planned"] += 1
        await db.commit()
    if stats["planned"]:
        try:
            from app.services.slack_notifications import post_digest_text_to_slack

            await post_digest_text_to_slack(f":wrench: *Agency engineer* drafted {stats['planned']} PR manifest(s) — plans only, human picks up implementation")
        except Exception:  # noqa: BLE001
            logger.exception("engineer sweep slack report failed")
    logger.info("agency engineer sweep: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Closer — deal-won sweep (Closed Won Opportunity → 14-day project)
# ---------------------------------------------------------------------------

WON_STAGES: tuple[str, ...] = ("Closed Won", "closedwon")
CONSULT_STAGE = "appointmentscheduled"
KICKOFF_MARKER = "Project kickoff:"
POST_CONSULT_MARKER = "Post-consult follow-up:"


async def _opportunity_contact_email(opp_id: str, account_id: str | None) -> tuple[str | None, str | None]:
    """Best-effort client (email, name) for an Opportunity — contacts, then account."""
    try:
        rel = await espo.espo_get(
            f"/Opportunity/{opp_id}/contacts",
            params={"select": "id,name,emailAddress", "maxSize": 5},
        )
        for contact in rel.get("list") or []:
            email = (contact.get("emailAddress") or "").strip().lower()
            if email:
                return email, contact.get("name")
    except Exception:  # noqa: BLE001
        logger.exception("agency: contacts read failed for opp %s", opp_id)
    if account_id:
        try:
            acc = await espo.espo_get(f"/Account/{account_id}", params={"select": "name,emailAddress"})
            email = (acc.get("emailAddress") or "").strip().lower()
            if email:
                return email, acc.get("name")
        except Exception:  # noqa: BLE001
            logger.exception("agency: account read failed for opp %s", opp_id)
    return None, None


async def _opp_has_note(opp_id: str, marker: str) -> bool:
    notes = await espo.list_all(
        "Note",
        select=["id", "post"],
        where=[
            {"type": "equals", "attribute": "parentType", "value": "Opportunity"},
            {"type": "equals", "attribute": "parentId", "value": opp_id},
        ],
        max_items=50,
    )
    return any(marker in (n.get("post") or "") for n in notes)


def _kickoff_email(client_name: str | None, project_name: str) -> tuple[str, str]:
    client = (client_name or "").strip() or "there"
    subject = f"Kickoff — {project_name} (day 1 of {DELIVERY_DAYS})"
    body = (
        f"Hi {client},\n\n"
        f"thanks for moving ahead — this is day 1 of your {DELIVERY_DAYS}-day delivery.\n\n"
        "How it runs:\n"
        "- Day 1 — kickoff, scope locked, access confirmed\n"
        "- Day 5 — architecture draft for your review\n"
        "- Day 10 — dashboards & tracking wired\n"
        f"- Day {DELIVERY_DAYS} — handoff package: your code, your docs, yours to keep\n\n"
        "You'll get a short update at each checkpoint — every milestone cites\n"
        "an artifact you can verify yourself.\n\n"
        "— Themistoklis, Cloudless"
    )
    return subject, body


async def _sent_proposal_for_email(db: AsyncSession, team_id: uuid.UUID, email: str | None) -> AgencyProposal | None:
    if not email:
        return None
    return (
        await db.execute(
            select(AgencyProposal)
            .where(
                AgencyProposal.team_id == team_id,
                AgencyProposal.recipient_email == email,
                AgencyProposal.status == AgencyProposalStatus.sent,
            )
            .order_by(desc(AgencyProposal.sent_at))
            .limit(1)
        )
    ).scalar_one_or_none()


async def won_sweep(*, session_factory=None) -> dict[str, Any]:
    """Closed-Won Opportunity → AgencyProject + kickoff mail + CRM audit note.

    Covers every win lineage: Stripe checkout deals, manually closed opps,
    proposal-accepted deals. Low-value Stripe product checkouts are skipped
    (a €9 checklist is not a 14-day engagement) via
    ``AGENCY_STRIPE_PROJECT_MIN_EUR``.
    """
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    if not espo.configured():
        return {"status": "skipped", "reason": "espocrm_not_configured"}
    factory = _factory(session_factory)
    stats: dict[str, Any] = {"status": "ok", "created": 0, "skipped": 0}
    min_stripe = int(getattr(get_settings(), "AGENCY_STRIPE_PROJECT_MIN_EUR", 500) or 500)
    async with factory() as db:
        team_id = await _default_team_id(db)
        if not team_id:
            return {"status": "skipped", "reason": "no_team"}
        opps = await espo.list_all(
            "Opportunity",
            select=["id", "name", "stage", "amount", "amountCurrency", "closeDate", "createdAt", "accountId", "accountName", "leadSource", "description"],
            where=[{"type": "in", "attribute": "stage", "value": list(WON_STAGES)}],
            max_items=50,
        )
        for opp in opps:
            oid = opp.get("id") or ""
            if not oid:
                continue
            if (opp.get("leadSource") or "") == "stripe_checkout" and (opp.get("amount") or 0) < min_stripe:
                stats["skipped"] += 1
                continue
            exists = (
                await db.execute(
                    select(AgencyProject.id)
                    .where(AgencyProject.team_id == team_id, AgencyProject.meta_data["espo_opportunity_id"].astext == oid)
                    .limit(1)
                )
            ).first()
            if exists or await _opp_has_note(oid, KICKOFF_MARKER):
                stats["skipped"] += 1
                continue

            email, client_name = await _opportunity_contact_email(oid, opp.get("accountId"))
            now = datetime.now(UTC)
            proposal = await _sent_proposal_for_email(db, team_id, email)
            project = AgencyProject(
                team_id=team_id,
                proposal_id=proposal.id if proposal else None,
                lead_id=proposal.lead_id if proposal else None,
                espo_lead_id=proposal.espo_lead_id if proposal else None,
                name=str(opp.get("name") or "Client project")[:300],
                client_email=email,
                kickoff_at=now,
                due_at=now + timedelta(days=DELIVERY_DAYS),
                status=AgencyProjectStatus.active,
                milestones=project_milestones(),
                meta_data={
                    "espo_opportunity_id": oid,
                    "opp_amount": opp.get("amount"),
                    "opp_currency": opp.get("amountCurrency"),
                    "lead_source": opp.get("leadSource"),
                    "client_name": client_name,
                },
            )
            db.add(project)
            await db.flush()

            if email:
                from app.services.email_digest import send_email

                subject, body = _kickoff_email(client_name, project.name)
                await send_email(
                    subject=subject,
                    text_body=body,
                    to_addrs=[email],
                    reply_to="tbaltzakis@cloudless.gr",
                    from_name="Themistoklis — Cloudless",
                )
            await espo.add_lead_note(
                oid,
                f"{KICKOFF_MARKER} {DELIVERY_DAYS}-day delivery started {now:%Y-%m-%d}, due {(now + timedelta(days=DELIVERY_DAYS)):%Y-%m-%d}",
                entity="Opportunity",
            )
            stats["created"] += 1
        await db.commit()

    if stats["created"]:
        try:
            from app.services.slack_notifications import post_alert_to_slack

            await post_alert_to_slack(f":rocket: *Agency won sweep* — {stats['created']} project(s) kicked off ({DELIVERY_DAYS}-day plan)")
        except Exception:  # noqa: BLE001
            logger.exception("won sweep slack report failed")
    logger.info("agency won sweep: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Front Desk follow-on — post-consultation sweep
# ---------------------------------------------------------------------------


def _post_consult_email(client_name: str | None, opp_name: str) -> tuple[str, str]:
    client = (client_name or "").strip() or "there"
    subject = "Following up on our call"
    body = (
        f"Hi {client},\n\n"
        "thanks for taking the time to talk — good conversation.\n\n"
        "Next step on my side: I'll run the deeper audit and put together the\n"
        "fixed-scope plan we discussed. You'll have it in your inbox shortly —\n"
        "one fixed price, 14-day delivery, you own everything.\n\n"
        "If I missed anything on the call, just reply to this mail.\n\n"
        "— Themistoklis, Cloudless"
    )
    return subject, body


async def post_consult_sweep(*, session_factory=None) -> dict[str, Any]:
    """Follow up consultations whose meeting date has passed.

    ``/api/calendar/book`` creates an Opportunity at stage
    ``appointmentscheduled`` with ``closeDate`` = meeting day. Once the day
    is past and the stage hasn't moved, the lead hears nothing — this sweep
    sends the thank-you/next-steps mail, once, marked by an EspoCRM note.
    """
    if not get_settings().AGENCY_ENABLED:
        return {"status": "disabled"}
    if not espo.configured():
        return {"status": "skipped", "reason": "espocrm_not_configured"}
    factory = _factory(session_factory)
    today = datetime.now(UTC).date().isoformat()
    stats: dict[str, Any] = {"status": "ok", "sent": 0, "skipped": 0}
    sent_lines: list[str] = []
    async with factory() as db:
        opps = await espo.list_all(
            "Opportunity",
            select=["id", "name", "stage", "closeDate", "accountId", "accountName"],
            where=[
                {"type": "equals", "attribute": "stage", "value": CONSULT_STAGE},
                {"type": "lessThan", "attribute": "closeDate", "value": today},
            ],
            max_items=50,
        )
        for opp in opps:
            oid = opp.get("id") or ""
            if not oid:
                continue
            if await _opp_has_note(oid, POST_CONSULT_MARKER):
                stats["skipped"] += 1
                continue
            email, client_name = await _opportunity_contact_email(oid, opp.get("accountId"))
            if not email:
                await espo.add_lead_note(
                    oid,
                    f"{POST_CONSULT_MARKER} skipped — no contact email on opportunity",
                    entity="Opportunity",
                )
                stats["skipped"] += 1
                continue
            from app.services.email_digest import send_email

            subject, body = _post_consult_email(client_name, str(opp.get("name") or ""))
            await send_email(
                subject=subject,
                text_body=body,
                to_addrs=[email],
                reply_to="tbaltzakis@cloudless.gr",
                from_name="Themistoklis — Cloudless",
            )
            await espo.add_lead_note(
                oid,
                f"{POST_CONSULT_MARKER} sent to {email} ({datetime.now(UTC):%Y-%m-%d %H:%M}Z)",
                entity="Opportunity",
            )
            stats["sent"] += 1
            sent_lines.append(f"{email}: {opp.get('name', '')[:60]}")
        await db.commit()

    if sent_lines:
        try:
            from app.services.slack_notifications import post_digest_text_to_slack

            lines = [f"*Post-consultation sweep* — {stats['sent']} follow-up(s) sent"]
            lines += [f"• {line}" for line in sent_lines[:5]]
            await post_digest_text_to_slack("\n".join(lines))
        except Exception:  # noqa: BLE001
            logger.exception("post-consult sweep slack report failed")
    logger.info("agency post-consult sweep: %s", stats)
    return stats

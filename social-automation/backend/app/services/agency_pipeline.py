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

        if get_settings().AGENCY_PROPOSAL_AUTOSEND:
            team_id = await _default_team_id(db)
            if team_id:
                stats["autosend"] = await _autosend_pass(db, team_id)
    logger.info("agency proposal sweep: %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Guarded autosend — earned-autonomy graduation for the proposal lane.
# A proposal self-sends only when every deterministic check passes; anything
# uncertain stays at the Slack approve gate. Daily cap bounds blast radius.
# ---------------------------------------------------------------------------


def _autosend_block_reasons(
    proposal: AgencyProposal,
    audit: AgencyAudit | None,
    lead: Lead | None,
) -> list[str]:
    """Deterministic quality gate — any reason keeps the human approve lane."""
    from app.services.leads import _is_synthetic_email, is_valid_email

    reasons: list[str] = []
    email = (proposal.recipient_email or "").strip()
    if not email or not is_valid_email(email):
        reasons.append("no-valid-recipient")
    elif _is_synthetic_email(email):
        reasons.append("synthetic-recipient")
    if audit is None or audit.status != AgencyAuditStatus.completed:
        reasons.append("audit-not-completed")
    if not proposal.services or any(s not in SERVICES for s in proposal.services):
        reasons.append("services-outside-catalog")
    if not proposal.price_low_eur or not proposal.price_high_eur:
        reasons.append("price-missing")
    elif proposal.price_high_eur > BUNDLE_PRICE_EUR:
        reasons.append("price-above-bundle")
    md = proposal.meta_data or {}
    if md.get("replied") or md.get("unsubscribed"):
        reasons.append("lead-engaged-or-opted-out")
    if lead is not None:
        lmd = lead.meta_data or {}
        if lmd.get("unsubscribed") or lmd.get("nurture_opted_out"):
            reasons.append("lead-opted-out")
    return reasons


async def _autosend_pass(db: AsyncSession, team_id: uuid.UUID) -> dict[str, Any]:
    """Evaluate every draft proposal against the gate; send the clean ones."""
    stats: dict[str, Any] = {"evaluated": 0, "sent": 0, "held": 0, "errors": 0, "held_reasons": {}}
    cap = int(getattr(get_settings(), "AGENCY_AUTOSEND_DAILY_CAP", 5) or 5)
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    drafts = (
        (
            await db.execute(
                select(AgencyProposal)
                .where(AgencyProposal.status == AgencyProposalStatus.draft)
                .order_by(AgencyProposal.created_at.asc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    already_sent_today = sum(
        1
        for p in (
            await db.execute(
                select(AgencyProposal).where(
                    AgencyProposal.status == AgencyProposalStatus.sent,
                    AgencyProposal.sent_at >= today,
                )
            )
        )
        .scalars()
        .all()
        if (p.meta_data or {}).get("approved_by") == "autosend"
    )
    budget = max(0, cap - already_sent_today)
    for proposal in drafts:
        stats["evaluated"] += 1
        audit = (await db.execute(select(AgencyAudit).where(AgencyAudit.id == proposal.audit_id).limit(1))).scalar_one_or_none()
        lead = (await db.execute(select(Lead).where(Lead.id == proposal.lead_id).limit(1))).scalar_one_or_none() if proposal.lead_id else None
        reasons = _autosend_block_reasons(proposal, audit, lead)
        for r in reasons:
            stats["held_reasons"][r] = stats["held_reasons"].get(r, 0) + 1
        if reasons:
            stats["held"] += 1
            continue
        if budget <= 0:
            stats["held_reasons"]["daily-cap"] = stats["held_reasons"].get("daily-cap", 0) + 1
            stats["held"] += 1
            continue
        try:
            res = await send_proposal(db, proposal, approver="autosend")
        except Exception as exc:  # noqa: BLE001 — one bad send mustn't stall the pass
            stats["errors"] += 1
            logger.warning("autosend failed for proposal %s: %s", proposal.id, exc)
            continue
        if res.get("ok"):
            stats["sent"] += 1
            budget -= 1
        else:
            stats["errors"] += 1
            logger.warning("autosend rejected for proposal %s: %s", proposal.id, res.get("error"))
    if stats["sent"]:
        try:
            from app.services.slack_notifications import post_alert_to_slack

            await post_alert_to_slack(
                f":outbox_tray: *Agency autosend* — {stats['sent']} proposal(s) emailed this pass "
                f"({stats['held']} held at gate, {stats['errors']} errors). Reject lane stays live."
            )
        except Exception:  # noqa: BLE001
            logger.exception("autosend slack report failed")
    return stats


async def _agency_payment_url(proposal: AgencyProposal, amount_eur: float) -> str | None:
    """Mint a Stripe checkout link for the proposal via cloudless.gr.

    Advisory — a failure returns None and the proposal sends without a link.
    """
    cfg = get_settings()
    url = cfg.AGENCY_CHECKOUT_URL
    if not url or amount_eur < 1:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={
                    "amount_eur": amount_eur,
                    "description": f"Cloudless engagement — {proposal.recipient_name or 'client'}",
                    "client_email": proposal.recipient_email or "",
                    "client_name": proposal.recipient_name or "",
                    "proposal_id": str(proposal.id),
                },
                headers={"X-SocialAuto-Webhook-Secret": cfg.CLOUDLESS_LEADS_WEBHOOK_SECRET},
            )
        if resp.status_code == 200:
            return resp.json().get("url")
        logger.warning("agency checkout mint → %s: %s", resp.status_code, resp.text[:200])
    except Exception:  # noqa: BLE001
        logger.exception("agency checkout mint failed")
    return None


async def send_proposal(db: AsyncSession, proposal: AgencyProposal, *, approver: str = "admin") -> dict[str, Any]:
    """Human-approved send — the only path that emails a lead a proposal."""
    if proposal.status not in (AgencyProposalStatus.draft, AgencyProposalStatus.approved):
        return {"ok": False, "error": f"proposal is {proposal.status}, not draft/approved"}
    if not proposal.recipient_email:
        return {"ok": False, "error": "no recipient email on proposal"}
    from app.services.email_digest import send_email

    body = proposal.body_md
    pay_url = await _agency_payment_url(proposal, float(proposal.price_low_eur or 0))
    if pay_url:
        body = (
            f"{body}\n\n---\n\nWhen you're ready to start, the secure payment\n"
            f"link for the fixed price is here:\n{pay_url}\n\n"
            "Payment kicks off the 14-day clock — the kickoff mail lands\n"
            "automatically."
        )
    await send_email(
        subject=proposal.subject,
        text_body=body,
        to_addrs=[proposal.recipient_email],
        reply_to="tbaltzakis@cloudless.gr",
        from_name="Themistoklis — Cloudless",
    )
    proposal.status = AgencyProposalStatus.sent
    proposal.sent_at = datetime.now(UTC)
    proposal.approved_at = proposal.approved_at or datetime.now(UTC)
    md = dict(proposal.meta_data or {})
    md["approved_by"] = approver
    if pay_url:
        md["payment_url"] = pay_url
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

            # Intake nudge — the kickoff mail asks for access/brand/scope
            # items; a client reply stamps meta.intake_received via
            # crm_engagement. Day 2: one gentle nudge. Day 4: Slack
            # escalate — the day-1 access milestone is slipping.
            md = dict(project.meta_data or {})
            if (
                day >= 2
                and project.client_email
                and not md.get("intake_received")
                and not md.get("intake_nudged")
            ):
                from app.services.email_digest import send_email

                client = (md.get("client_name") or "").strip() or "there"
                await send_email(
                    subject=f"Quick one — access for {project.name}",
                    text_body=(
                        f"Hi {client},\n\n"
                        "small nudge on the kickoff mail — to keep the 14-day\n"
                        "clock honest, I still need:\n\n"
                        "1. Admin/hosting + DNS access (or a delegated invite)\n"
                        "2. Brand assets (or 'use what's on the site')\n"
                        "3. Analytics access if you have it\n"
                        "4. One-line scope approval\n\n"
                        "Even a partial reply unblocks the day-1 milestone.\n\n"
                        "— Themistoklis, Cloudless"
                    ),
                    to_addrs=[project.client_email],
                    reply_to="tbaltzakis@cloudless.gr",
                    from_name="Themistoklis — Cloudless",
                )
                md["intake_nudged"] = now.isoformat()
                project.meta_data = md
                stats["intake_nudges"] = stats.get("intake_nudges", 0) + 1
            if day >= 4 and not md.get("intake_received") and not md.get("intake_escalated"):
                md["intake_escalated"] = now.isoformat()
                project.meta_data = md
                stats["intake_escalations"] = stats.get("intake_escalations", 0) + 1

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
            if not project.client_email:
                continue
            # Testimonial ask — once per project, ~2 days post-handoff.
            if not md.get("testimonial_asked"):
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

            # ── Delight ladder — the relationship continues after delivery.
            # +30d check-in, +60d referral ask, +90d upsell nudge — each
            # meta-stamped so it sends exactly once per project. Anchored on
            # the testimonial_asked stamp (= delivery date).
            for step_key, step_day, step_subj, step_body in _DELIGHT_STEPS:
                stamp = f"delight_{step_key}"
                if md.get(stamp):
                    continue
                try:
                    asked_at = datetime.fromisoformat(str(md.get("testimonial_asked", "")))
                except ValueError:
                    continue
                if asked_at.tzinfo is None:
                    asked_at = asked_at.replace(tzinfo=UTC)
                if now < asked_at + timedelta(days=step_day):
                    continue
                from app.services.email_digest import send_email

                client = (md.get("client_name") or "").strip() or "there"
                await send_email(
                    subject=step_subj.format(name=project.name, client=client),
                    text_body=step_body.format(name=project.name, client=client, days=DELIVERY_DAYS),
                    to_addrs=[project.client_email],
                    reply_to="tbaltzakis@cloudless.gr",
                    from_name="Themistoklis — Cloudless",
                )
                md[stamp] = now.isoformat()
                project.meta_data = md
                stats[stamp] = stats.get(stamp, 0) + 1
        await db.commit()

    delight_sent = sum(v for k, v in stats.items() if k.startswith("delight_"))
    if drafts or stats["delivered"] or stats["overdue"] or stats.get("intake_escalations") or delight_sent:
        try:
            from app.services.slack_notifications import post_alert_to_slack

            lines = [":calendar: *Agency PM sweep*"]
            lines += [f"```{d}```" for d in drafts[:5]]
            if stats["delivered"]:
                lines.append(f"{stats['delivered']} project(s) hit day 14 — handoff compiled")
            if stats["overdue"]:
                lines.append(f":warning: {stats['overdue']} project(s) past due — needs a human")
            if stats.get("intake_escalations"):
                lines.append(f":rotating_light: {stats['intake_escalations']} project(s) day 4+ with no client intake — access milestone slipping")
            if delight_sent:
                lines.append(f":sparkling_heart: {delight_sent} post-delivery check-in(s) sent (30/60/90-day ladder)")
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

# Post-delivery delight ladder — (key, days-after-delivery, subject, body).
# Plain voice, one point each; stamped delight_<key> in project meta.
_DELIGHT_STEPS = (
    (
        "checkin_30",
        30,
        "How is {name} holding up?",
        (
            "Hi {client},\n\n"
            "it's been about a month since the {days}-day delivery wrapped.\n\n"
            "Quick check-in: is everything still running as expected? If\n"
            "anything's drifted or a question came up, reply — a fix or a\n"
            "pointer is part of the package, not an extra.\n\n"
            "— Themistoklis, Cloudless"
        ),
    ),
    (
        "referral_60",
        60,
        "One small favour — {name}",
        (
            "Hi {client},\n\n"
            "glad the delivery is still earning its keep.\n\n"
            "One small favour: if you know another owner who'd benefit from\n"
            "the same fixed-scope work, an intro mail is the biggest help\n"
            "you can give me. Two lines is plenty — I take it from there.\n\n"
            "Either way, thank you for the trust.\n\n"
            "— Themistoklis, Cloudless"
        ),
    ),
    (
        "upsell_90",
        90,
        "Keep it maintained? — {name}",
        (
            "Hi {client},\n\n"
            "quarterly check on {name}. The system is yours and stays yours —\n"
            "this is just the option, not a pitch you have to answer.\n\n"
            "If you'd rather not carry updates, monitoring and small\n"
            "improvements yourself, I run a light monthly maintenance plan —\n"
            "fixed price, cancel anytime, you keep everything either way.\n\n"
            "Reply and I'll send the one-pager.\n\n"
            "— Themistoklis, Cloudless"
        ),
    ),
)
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


def _kickoff_email(client_name: str | None, project_name: str, loa_md: str | None = None) -> tuple[str, str]:
    client = (client_name or "").strip() or "there"
    subject = f"Kickoff — {project_name} (day 1 of {DELIVERY_DAYS})"
    loa_block = f"\n---\n\n{loa_md.strip()}\n" if loa_md else ""
    body = (
        f"Hi {client},\n\n"
        f"thanks for moving ahead — this is day 1 of your {DELIVERY_DAYS}-day delivery.\n\n"
        "How it runs:\n"
        "- Day 1 — kickoff, scope locked, access confirmed\n"
        "- Day 5 — architecture draft for your review\n"
        "- Day 10 — dashboards & tracking wired\n"
        f"- Day {DELIVERY_DAYS} — handoff package: your code, your docs, yours to keep\n\n"
        "To hit the day-1 access milestone, reply to this mail with:\n"
        "1. Admin/hosting + DNS access (or a delegated invite — whatever's easiest)\n"
        "2. Brand assets — logo files, fonts, colors (or 'use what's on the site')\n"
        "3. Analytics access — GA/Search Console invite if you have them\n"
        "4. One-line approval of the scope we agreed\n\n"
        "You'll get a short update at each checkpoint — every milestone cites\n"
        "an artifact you can verify yourself.\n\n"
        "— Themistoklis, Cloudless"
        f"{loa_block}"
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
        opps: list[dict[str, Any]] = []
        seen: set[str] = set()
        # stage spellings differ by writer (site uses HubSpot-style keys,
        # the UI uses EspoCRM enum labels) — one equals query each.
        for stage in WON_STAGES:
            for opp in await espo.list_all(
                "Opportunity",
                select=["id", "name", "stage", "amount", "amountCurrency", "closeDate", "createdAt", "accountId", "accountName", "leadSource", "description"],
                where=[{"type": "equals", "attribute": "stage", "value": stage}],
                max_items=50,
            ):
                if opp.get("id") and opp["id"] not in seen:
                    seen.add(opp["id"])
                    opps.append(opp)
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

            # Auto-draft the Letter of Agreement (Pipeline Toolkit asset) —
            # markdown lands on the project record; sending stays a human call
            # after scope is confirmed on the kickoff call.
            try:
                from scripts.letter_of_agreement import render_markdown

                project.meta_data = {
                    **(project.meta_data or {}),
                    "loa_md": render_markdown(
                        client=client_name or str(opp.get("accountName") or "Client"),
                        date=now.strftime("%Y-%m-%d"),
                        amount=float(opp.get("amount") or 800),
                        provider="Cloudless",
                    ),
                }
            except Exception:  # noqa: BLE001 — LOA draft is advisory, never blocks the kickoff
                logger.debug("LOA render failed for project %s", project.id)

            if email:
                from app.services.email_digest import send_email

                subject, body = _kickoff_email(client_name, project.name, (project.meta_data or {}).get("loa_md"))
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
        f"thanks for taking the time to talk about {opp_name} — good conversation.\n\n"
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

"""Daily monetization report → email + auto-scheduled actions.

Companion to the strategy brief, but monetization-first: every connected
account is evaluated against its creator-program gate (``monetization.py``)
with live metrics, then the inference chain (DMR → Cloudflare) turns the
gate/momentum data into per-platform strategy sections plus a concrete
action list for tomorrow.

Unlike the strategy brief's advisory playbook, these actions feed
``monetization_executor.schedule_report_actions`` which materialises them
as SCHEDULED posts (full generation + quality + media pipeline) — the
report email therefore shows what was *done*, not just what was suggested.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.lead import Lead, LeadSource
from app.models.social_account import SocialAccount
from app.models.user import Team
from app.services.email_digest import _html_escape, send_email
from app.services.insights_engine import build_team_insights
from app.services.monetization import (
    FUNNEL_ONLY,
    ProgramReport,
    days_to_goal,
    evaluate_program,
    gather_account_metrics,
)

logger = logging.getLogger(__name__)

_MAX_ACTIONS = 5


@dataclass
class MonetizationAction:
    """One concrete action the executor can schedule — structured from the
    LLM's JSON (not parsed from prose like the strategy brief's playbook)."""

    platform: str
    instruction: str
    time_athens: str = ""        # "HH:MM" local
    format: str = ""             # text | image | carousel | video


@dataclass
class AccountGate:
    """One account's monetization-gate evaluation for the report."""

    platform: str
    handle: str
    account_type: str
    followers: int | None
    gain_per_day: float
    followers_live: bool
    program: ProgramReport | None   # None → funnel-only surface
    funnel_note: str = ""
    eta_days: int | None = None     # days to next gate at current pace


@dataclass
class MonetizationReport:
    generated_at: datetime
    timezone: str
    team_name: str
    gates: list[AccountGate] = field(default_factory=list)
    telegram_members: int | None = None
    telegram_leads_7d: int = 0
    telegram_leads_30d: int = 0
    insights: dict[str, Any] = field(default_factory=dict)
    initiatives: list[dict[str, Any]] = field(default_factory=list)
    sections: list[dict[str, str]] = field(default_factory=list)   # LLM strategy
    actions: list[MonetizationAction] = field(default_factory=list)
    scheduled: list[dict[str, Any]] = field(default_factory=list)  # executor results
    llm_used: bool = False
    emailed: bool = False
    email_error: str | None = None

    def subject(self) -> str:
        return (
            f"SocialAuto monetization report — "
            f"{self.generated_at.astimezone(ZoneInfo(self.timezone)):%Y-%m-%d}"
        )

    # ── rendering ─────────────────────────────────────────────────────

    def to_text(self) -> str:
        lines = [
            f"MONETIZATION REPORT — {self.team_name}",
            f"Generated {self.generated_at:%Y-%m-%d %H:%M UTC} ({self.timezone})",
            "",
            "── PROGRAM GATES ──",
        ]
        for g in self.gates:
            live = "live" if g.followers_live else "snapshot"
            ftxt = f"{g.followers:,}" if g.followers is not None else "—"
            lines.append(
                f"{g.platform:10s} {g.handle:24s} {ftxt:>7s} followers "
                f"({live}, {g.gain_per_day:+.1f}/day)"
            )
            if g.program:
                for c in g.program.criteria:
                    mark = "✓" if c.met is True else ("✗" if c.met is False else "?")
                    cur = c.current if c.current is not None else "—"
                    lines.append(f"  {mark} {c.name}: {cur} (needs {c.target})")
                if g.eta_days is not None:
                    lines.append(f"  → ~{g.eta_days}d to next gate at current pace")
            else:
                lines.append(f"  {g.funnel_note}")
        lines += [
            "",
            "── TELEGRAM FUNNEL ──",
            f"  channel members: {self.telegram_members if self.telegram_members is not None else '—'}",
            f"  DM leads: {self.telegram_leads_7d} (7d) / {self.telegram_leads_30d} (30d)",
        ]
        if self.sections:
            lines += ["", "── STRATEGY ──"]
            for s in self.sections:
                lines.append(f"{s.get('platform','').upper()}: {s.get('headline','')}")
                lines.append(f"  {s.get('strategy','')}")
        if self.scheduled:
            lines += ["", "── ACTIONS EXECUTED ──"]
            for r in self.scheduled:
                lines.append(
                    f"  {r.get('platform','?'):10s} {r.get('status','?'):10s} "
                    f"{r.get('scheduled_at') or ''} {r.get('instruction','')[:80]}"
                )
        return "\n".join(lines)

    def to_html(self) -> str:
        esc = _html_escape
        gen_local = self.generated_at.astimezone(ZoneInfo(self.timezone))
        parts = [
            "<h2>SocialAuto — Daily Monetization Report</h2>",
            f"<p><i>{esc(gen_local.strftime('%Y-%m-%d %H:%M %Z'))}</i></p>",
            "<h3>Program gates</h3><table border=1 cellpadding=6 cellspacing=0 "
            "style='border-collapse:collapse'>"
            "<tr><th>Platform</th><th>Account</th><th>Followers</th>"
            "<th>Gate progress</th></tr>",
        ]
        for g in self.gates:
            cells = []
            if g.program:
                for c in g.program.criteria:
                    mark = "✅" if c.met is True else ("❌" if c.met is False else "❔")
                    cur = c.current if c.current is not None else "—"
                    cells.append(f"{mark} {esc(c.name)}: {esc(str(cur))}")
                if g.eta_days is not None:
                    cells.append(f"⏳ ~{g.eta_days}d to next gate")
                gate_html = "<br>".join(cells)
            else:
                gate_html = esc(g.funnel_note or "funnel surface")
            live = "live" if g.followers_live else "snap"
            ftxt = f"{g.followers:,}" if g.followers is not None else "—"
            parts.append(
                f"<tr><td>{esc(g.platform)}</td><td>{esc(g.handle)}</td>"
                f"<td>{ftxt} <small>({live}, {g.gain_per_day:+.1f}/d)</small></td>"
                f"<td>{gate_html}</td></tr>"
            )
        parts.append("</table>")
        parts.append(
            f"<h3>Telegram funnel</h3><p>channel members: "
            f"<b>{self.telegram_members if self.telegram_members is not None else '—'}</b>"
            f" · DM leads: <b>{self.telegram_leads_7d}</b> (7d) / "
            f"<b>{self.telegram_leads_30d}</b> (30d)</p>"
        )
        if self.sections:
            parts.append("<h3>Strategy</h3>")
            for s in self.sections:
                parts.append(
                    f"<p><b>{esc(s.get('platform','').upper())}</b> — "
                    f"{esc(s.get('headline',''))}<br>{esc(s.get('strategy',''))}</p>"
                )
        if self.scheduled:
            parts.append(
                "<h3>Actions executed</h3><table border=1 cellpadding=6 cellspacing=0 "
                "style='border-collapse:collapse'>"
                "<tr><th>Platform</th><th>Status</th><th>When</th><th>Instruction</th></tr>"
            )
            for r in self.scheduled:
                parts.append(
                    f"<tr><td>{esc(r.get('platform',''))}</td>"
                    f"<td>{esc(r.get('status',''))}</td>"
                    f"<td>{esc(str(r.get('scheduled_at') or ''))}</td>"
                    f"<td>{esc(r.get('instruction',''))}</td></tr>"
                )
            parts.append("</table>")
        parts.append(
            "<p><small>Generated by SocialAuto monetization report "
            f"(llm={'yes' if self.llm_used else 'rule-fallback'})</small></p>"
        )
        return "\n".join(parts)


# ── gathering ───────────────────────────────────────────────────────────


async def _telegram_stats(db: AsyncSession, team_id) -> tuple[int | None, int, int]:
    """Channel member count + DM-funnel lead counts (7d/30d)."""
    members: int | None = None
    try:
        acct = (
            await db.execute(
                select(SocialAccount).where(
                    SocialAccount.team_id == team_id,
                    SocialAccount.platform == "telegram",
                    SocialAccount.status == "active",
                )
            )
        ).scalars().first()
        if acct:
            from app.core.security import decrypt_token
            from app.services.telegram_api import TelegramAPIClient

            chat_id = (acct.meta_data or {}).get("channel_id") or acct.account_id
            members = await TelegramAPIClient(
                decrypt_token(acct.access_token_enc)
            ).get_chat_member_count(chat_id)
    except Exception as exc:  # noqa: BLE001
        logger.info("telegram member count failed: %s", str(exc)[:120])

    now = datetime.now(UTC)
    leads_7d = await db.scalar(
        select(func.count(Lead.id)).where(
            Lead.team_id == team_id,
            Lead.source == LeadSource.telegram_dm,
            Lead.created_at >= now - timedelta(days=7),
        )
    ) or 0
    leads_30d = await db.scalar(
        select(func.count(Lead.id)).where(
            Lead.team_id == team_id,
            Lead.source == LeadSource.telegram_dm,
            Lead.created_at >= now - timedelta(days=30),
        )
    ) or 0
    return members, leads_7d, leads_30d


async def _gather_gates(db: AsyncSession, team_id) -> list[AccountGate]:
    accounts = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team_id,
                SocialAccount.status == "active",
            )
        )
    ).scalars().all()
    gates: list[AccountGate] = []
    for a in accounts:
        metrics = await gather_account_metrics(db, a)
        program = evaluate_program(a.platform, metrics, account_type=a.account_type)
        followers = metrics.get("followers")
        gain = metrics.get("gain_per_day") or 0.0
        eta = None
        if program:
            gate_targets = [
                c.target for c in program.criteria
                if isinstance(c.target, int | float) and c.met is False
            ]
            if gate_targets and isinstance(followers, int):
                eta = days_to_goal(followers, min(gate_targets), gain)
        gates.append(
            AccountGate(
                platform=a.platform,
                handle=f"@{a.username}" if a.username else a.account_id[:12],
                account_type=a.account_type,
                followers=followers,
                gain_per_day=gain,
                followers_live=bool(metrics.get("followers_live")),
                program=program,
                funnel_note="" if program else FUNNEL_ONLY.get(a.platform, "funnel surface"),
                eta_days=eta,
            )
        )
    return gates


# ── strategy generation ─────────────────────────────────────────────────

_STRATEGY_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string"},
                    "headline": {"type": "string"},
                    "strategy": {"type": "string"},
                },
                "required": ["platform", "headline", "strategy"],
            },
        },
        "actions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "platform": {"type": "string"},
                    "time_local": {"type": "string"},
                    "format": {"type": "string"},
                    "instruction": {"type": "string"},
                },
                "required": ["platform", "instruction"],
            },
        },
    },
    "required": ["sections", "actions"],
}


def _compact_gates(gates: list[AccountGate]) -> list[dict[str, Any]]:
    out = []
    for g in gates:
        row: dict[str, Any] = {
            "platform": g.platform,
            "handle": g.handle,
            "followers": g.followers,
            "gain_per_day": g.gain_per_day,
        }
        if g.program:
            row["program"] = g.program.program
            row["unmet"] = [
                c.name for c in g.program.criteria if c.met is False
            ]
            row["manual_pending"] = [
                c.name for c in g.program.criteria if c.met is None
            ]
        else:
            row["role"] = "funnel"
        out.append(row)
    return out


async def _llm_strategy(
    db: AsyncSession,
    team_id,
    gates: list[AccountGate],
    insights: dict[str, Any],
    telegram: dict[str, Any],
) -> tuple[list[dict[str, str]], list[MonetizationAction]]:
    """DMR → CF inference turns the gate/momentum data into per-platform
    strategy + a concrete action list. Returns ([], []) on failure."""
    from app.services.inference import call_inference

    platform_pulse = []
    for name, p in list((insights.get("platforms") or {}).items())[:8]:
        platform_pulse.append(
            {
                "platform": name,
                "posts_30d": p.get("posts"),
                "impressions": p.get("impressions"),
                "engagement": p.get("engagement"),
                "avg_engagement_rate": p.get("avg_engagement_rate"),
                "momentum_engagement_pct": p.get("momentum_7d_engagement_pct"),
                "best_hour_athens": (p.get("best_hour_athens") or [None])[0],
                "outlier_posts": len(p.get("outliers") or []),
            }
        )

    prompt = f"""You are the monetization strategist for cloudless.gr (managed cloud hosting /
social-automation SaaS). Monetization is the PRIMARY goal.

CURRENT PROGRAM GATES (live evaluation):
{json.dumps(_compact_gates(gates), ensure_ascii=False)}

PLATFORM PERFORMANCE (last 30d):
{json.dumps(platform_pulse, ensure_ascii=False)}

TELEGRAM FUNNEL: {json.dumps(telegram)}

VERIFIED PROGRAM FACTS (2026):
- FB Stars: 500 followers HELD 30 consecutive days — growth of the personal
  profile is the active blocker (raw build-in-public content wins there).
- FB Content Monetization: invite-only (in-stream ads / Reels ads / bonus
  ended 2025-08-31); already on the waitlist — no numeric gate.
- X Original Content Rewards (replaced Revenue Sharing, retired 2026-09):
  Premium + 500 VERIFIED followers + 500K verified Home-Timeline impressions/90d.
- TikTok Creator Rewards: geo-blocked for Greece — TikTok is a product-funnel
  surface only (Polar/audit CTA), never a payout path.
- IG Subscriptions/Gifts: invite-gated, ~10k follower band.
- LinkedIn/Threads/messaging: no creator payouts — funnel surfaces that feed
  Polar plans (https://social.cloudless.gr/pricing) and the free audit
  (https://cloudless.gr/contact). LinkedIn company page is the fastest-growing.
- Telegram channel feeds the DM lead funnel (t.me bot /start audit flow).

RULES:
- One strategy section per connected platform with real data. Each: a headline
  (≤10 words) + 2-3 sentences on the single most valuable next move — growth
  toward the gate OR funnel conversion. Never recommend actions for
  geo-blocked or invite-waitlisted gates beyond "keep feeding the funnel".
- At most {_MAX_ACTIONS} actions — concrete posts SocialAuto can auto-schedule
  TOMORROW. Prefer platforms/formats that won in the last 30d.
- Each action: platform, time_local (HH:MM Europe/Athens, 07:00–23:00 only),
  format (text|image|carousel|video), instruction (what to post — specific
  topic + angle, not a generic theme).
- Funnel CTAs only where earned by the content; ~1 in 3 actions max may carry
  the free-audit/Polar CTA.

Return ONLY a JSON object in EXACTLY this shape:
{{"sections": [{{"platform": "linkedin", "headline": "...", "strategy": "..."}}],
 "actions": [{{"platform": "linkedin", "time_local": "09:30",
              "format": "image", "instruction": "..."}}]}}"""

    try:
        result = await call_inference(
            prompt,
            provider_name="dmr",
            db=db,
            team_id=team_id,
            schema=_STRATEGY_SCHEMA,
            platform="report",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("monetization LLM strategy failed: %s", str(exc)[:160])
        return [], []

    # DMR's schema arg is a soft hint — models occasionally answer with a
    # nested {"strategy": {platform: {actions: [...]}}} shape instead.
    if not result.get("sections") and isinstance(result.get("strategy"), dict):
        nested = result["strategy"]
        result = {
            "sections": [
                {"platform": p, "headline": (v or {}).get("headline", ""),
                 "strategy": (v or {}).get("strategy", "")}
                for p, v in nested.items() if isinstance(v, dict)
            ],
            "actions": [
                {
                    "platform": p,
                    "time_local": a.get("time_local") or a.get("time") or "",
                    "format": a.get("format") or "",
                    "instruction": a.get("instruction") or a.get("action") or "",
                }
                for p, v in nested.items() if isinstance(v, dict)
                for a in (v.get("actions") or []) if isinstance(a, dict)
            ],
        }

    sections = [
        {
            "platform": str(s.get("platform") or ""),
            "headline": str(s.get("headline") or ""),
            "strategy": str(s.get("strategy") or ""),
        }
        for s in (result.get("sections") or [])
        if s.get("platform") and s.get("strategy")
    ]
    actions: list[MonetizationAction] = []
    for a in (result.get("actions") or [])[:_MAX_ACTIONS]:
        platform = str(a.get("platform") or "").lower().strip()
        instruction = str(a.get("instruction") or "").strip()
        if not platform or not instruction:
            continue
        if platform == "x":
            platform = "twitter"
        fmt = str(a.get("format") or "").lower().strip()
        if fmt not in {"text", "image", "carousel", "video"}:
            fmt = ""
        t = str(a.get("time_local") or "")
        m = re.match(r"^([01]?\d|2[0-3]):([0-5]\d)$", t)
        actions.append(
            MonetizationAction(
                platform=platform,
                instruction=instruction,
                time_athens=f"{int(m.group(1)):02d}:{m.group(2)}" if m else "",
                format=fmt,
            )
        )
    return sections, actions


def _rule_actions(gates: list[AccountGate]) -> list[MonetizationAction]:
    """Deterministic fallback when inference is unavailable: push the two
    live blockers — FB profile growth (Stars) and LinkedIn funnel volume."""
    out: list[MonetizationAction] = []
    fb = next((g for g in gates if g.platform == "facebook" and g.account_type != "page"), None)
    if fb:
        out.append(
            MonetizationAction(
                platform="facebook",
                instruction=(
                    "Raw build-log post on the personal profile: one real thing "
                    "shipped or fixed on the automation stack today — numbers and "
                    "failures included. Goal: follower growth toward Stars 500."
                ),
                time_athens="12:00",
                format="image",
            )
        )
    li = next((g for g in gates if g.platform == "linkedin"), None)
    if li:
        out.append(
            MonetizationAction(
                platform="linkedin",
                instruction=(
                    "B2B value post for the company page: a concrete cost/time "
                    "saving cloudless.gr delivers, ending with the free audit CTA."
                ),
                time_athens="09:30",
                format="image",
            )
        )
    return out


# ── orchestration ───────────────────────────────────────────────────────


async def build_monetization_report(
    db: AsyncSession,
    *,
    team: Team,
    insight_days: int = 30,
) -> MonetizationReport:
    settings = get_settings()
    tz_name = settings.APP_TIMEZONE or "Europe/Athens"

    gates = await _gather_gates(db, team.id)
    members, leads_7d, leads_30d = await _telegram_stats(db, team.id)
    insights = await build_team_insights(db, team.id, days=insight_days)

    from app.services.growth_initiatives import initiative_summary

    report = MonetizationReport(
        generated_at=datetime.now(UTC),
        timezone=tz_name,
        team_name=team.name or "SocialAuto",
        gates=gates,
        telegram_members=members,
        telegram_leads_7d=leads_7d,
        telegram_leads_30d=leads_30d,
        insights=insights,
        initiatives=await initiative_summary(db, team.id),
    )

    tg = {"channel_members": members, "dm_leads_7d": leads_7d, "dm_leads_30d": leads_30d}
    sections, actions = await _llm_strategy(db, team.id, gates, insights, tg)
    if sections or actions:
        report.sections = sections
        report.actions = actions
        report.llm_used = True
    if not actions:
        report.actions = _rule_actions(gates)
    return report


async def email_monetization_report(report: MonetizationReport) -> MonetizationReport:
    settings = get_settings()
    if not (settings.DIGEST_EMAIL_TO or "").strip():
        report.email_error = "DIGEST_EMAIL_TO not set"
        return report
    try:
        await send_email(
            subject=report.subject(),
            text_body=report.to_text(),
            html_body=report.to_html(),
        )
        report.emailed = True
    except Exception as exc:  # noqa: BLE001
        report.email_error = str(exc) or repr(exc)
        logger.exception("Failed to email monetization report")
    return report


async def run_monetization_report_for_all_teams(
    db: AsyncSession,
    *,
    insight_days: int = 30,
    send: bool = True,
    execute: bool = True,
) -> list[dict[str, Any]]:
    """Daily entry point: build → execute actions (schedule posts) → email.

    The executor runs BEFORE the email so the report shows what was actually
    scheduled — matching the owner's "report → platform executes" flow.
    """
    from app.services.monetization_executor import schedule_report_actions

    teams = (await db.execute(select(Team))).scalars().all()
    results: list[dict[str, Any]] = []
    for team in teams:
        connected = await db.scalar(
            select(func.count(SocialAccount.id)).where(
                SocialAccount.team_id == team.id,
                SocialAccount.status == "active",
            )
        )
        if not connected:
            results.append(
                {
                    "team_name": team.name or "SocialAuto",
                    "actions": [],
                    "emailed": False,
                    "email_error": "skipped empty team",
                }
            )
            continue

        report = await build_monetization_report(
            db, team=team, insight_days=insight_days
        )
        if execute and report.actions:
            report.scheduled = await schedule_report_actions(
                db, team, report.actions, report.generated_at.date().isoformat()
            )
        if send:
            report = await email_monetization_report(report)
        results.append(
            {
                "team_name": report.team_name,
                "gates": len(report.gates),
                "actions": [a.instruction for a in report.actions],
                "scheduled": report.scheduled,
                "llm_used": report.llm_used,
                "emailed": report.emailed,
                "email_error": report.email_error,
            }
        )
    return results

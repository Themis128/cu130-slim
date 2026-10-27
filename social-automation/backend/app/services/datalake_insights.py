"""Datalake read-back loop — turn exported data into decisions + actions.

The export side (``datalake_export``) pushes snapshots to R2; this service is
the *consumer*: it reads the live tables (same source the lake snapshots) and
converts them into platform-facing actions:

- **survey pain → content** — free-text answers (the "what embarrasses you"
  question and friends) become keywords; the top topics enqueue draft posts on
  the right platform with the right funnel CTA.
- **price anchor → funnel weight** — the worth-per-month answers set the
  suggested retainer band recorded on the signal event.
- **audit interest → escalation** — every ``audit_interest=Yes`` without a
  booked audit re-pings the human gate (Slack), not a blind auto-book.
- **signal event → next export** — computed signals are persisted as an
  ``AnalyticsEvent`` (``event_type="datalake_signals"``) so the next datalake
  export carries them and downstream gold layers can react.

Pure-function core (``compute_survey_signals`` / ``pick_actions``) is
unit-testable without a DB; ``build_signals``/``apply_signals`` are the async
boundaries.
"""
from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.analytics import AnalyticsEvent
from app.models.email_campaign import SurveyResponse
from app.models.lead import Lead
from app.models.linkedin_ads import AdDailyMetric

# ── Signal extraction ────────────────────────────────────────────────────────

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "is", "it",
    "my", "our", "we", "i", "that", "this", "with", "at", "be", "have", "has",
    "not", "no", "yes", "the", "ena", "kai", "to", "th", "thn", "gia", "se",
}

# Questions whose free-text answers carry content fuel
_PAIN_QIDS = {"embarrass", "vendors", "sell_online_barrier", "it_support"}


def compute_survey_signals(responses: list[dict]) -> dict[str, Any]:
    """Aggregate survey answer dicts into decision signals."""
    words: Counter[str] = Counter()
    audit_yes: list[str] = []
    price_anchors: list[float] = []
    objections: Counter[str] = Counter()
    for r in responses:
        answers = r.get("answers") or {}
        email = (r.get("email") or "").lower()
        if str(answers.get("audit_interest", "")).strip().lower() == "yes":
            audit_yes.append(email)
        for qid, val in answers.items():
            sval = str(val)
            if qid.startswith("worth") or "month" in qid:
                for num in re.findall(r"\d{2,5}", sval):
                    price_anchors.append(float(num))
            elif "hesitate" in qid or "objection" in qid:
                objections[sval[:80]] += 1
            elif any(k in qid for k in _PAIN_QIDS):
                for w in re.findall(r"[A-Za-z\u0370-\u03ff]{4,}", sval.lower()):
                    if w not in _STOPWORDS:
                        words[w] += 1
    median_price = sorted(price_anchors)[len(price_anchors) // 2] if price_anchors else None
    return {
        "responses": len(responses),
        "audit_yes": audit_yes,
        "pain_topics": words.most_common(10),
        "price_anchor_median": median_price,
        "top_objections": objections.most_common(5),
    }


def compute_lead_signals(leads: list[dict]) -> dict[str, Any]:
    """Lead rows → per-source volume + interest/company-size mix (30d)."""
    by_source: Counter[str] = Counter()
    by_interest: Counter[str] = Counter()
    sizes: Counter[str] = Counter()
    for l in leads:
        by_source[l.get("source") or "unknown"] += 1
        if l.get("interest"):
            by_interest[l["interest"]] += 1
        if l.get("company_size"):
            sizes[l["company_size"]] += 1
    return {
        "lead_volume_30d": len(leads),
        "lead_sources": by_source.most_common(),
        "lead_interests": by_interest.most_common(),
        "lead_sizes": sizes.most_common(),
    }


def compute_funnel_signals(rows: list[dict]) -> dict[str, Any]:
    """web-analytics UTM rows → per-platform/campaign view→click→lead rates."""
    out: dict[str, Any] = {}
    for r in rows:
        plat = r.get("platform") or "unknown"
        d = out.setdefault(plat, {"views": 0, "clicks": 0, "leads": 0})
        d["views"] += int(r.get("views") or 0)
        d["clicks"] += int(r.get("clicks") or 0)
        d["leads"] += int(r.get("leads") or 0)
    best = max(out.items(), key=lambda kv: kv[1]["leads"], default=None)
    return {"platform_funnels": out,
            "best_converting": best[0] if best else None}


def compute_ad_signals(daily: list[dict]) -> dict[str, Any]:
    """AdDailyMetric rows → spend, leads, CPL per campaign (trailing window)."""
    per_campaign: dict[str, dict[str, float]] = {}
    for r in daily:
        d = per_campaign.setdefault(r.get("campaign") or "?", {"spend": 0.0, "leads": 0, "impressions": 0})
        d["spend"] += float(r.get("spend_eur") or 0)
        d["leads"] += int(r.get("leads") or 0)
        d["impressions"] += int(r.get("impressions") or 0)
    for c in per_campaign.values():
        c["cpl"] = round(c["spend"] / c["leads"], 2) if c["leads"] else None
    return {"ad_campaigns": per_campaign}


def pick_actions(signals: dict[str, Any]) -> list[dict[str, Any]]:
    """Decide what the stack should do next — returns action dicts."""
    actions: list[dict[str, Any]] = []
    best = signals.get("best_converting")
    if best:
        actions.append({
            "type": "platform_focus", "platform": best,
            "reason": f"{best} funnels the most leads — weight next posts there",
        })
    for src, n in signals.get("lead_sources", [])[:2]:
        actions.append({
            "type": "lead_source", "source": src, "volume": n,
            "reason": f"{src} produced {n} leads (30d) — keep feeding it",
        })
    for camp, c in (signals.get("ad_campaigns") or {}).items():
        if c.get("cpl") is not None and c["cpl"] > 30:
            actions.append({
                "type": "ad_efficiency", "campaign": camp, "cpl": c["cpl"],
                "reason": f"{camp} CPL €{c['cpl']:.0f} — over the €30 LinkedIn benchmark",
            })
    for word, n in signals.get("pain_topics", [])[:3]:
        actions.append({
            "type": "content_topic", "topic": word, "mentions": n,
            "reason": f"'{word}' showed up {n}x in survey pain answers",
        })
    if signals.get("audit_yes"):
        actions.append({
            "type": "audit_escalation",
            "emails": signals["audit_yes"],
            "reason": f"{len(signals['audit_yes'])} audit request(s) pending booking",
        })
    if signals.get("price_anchor_median"):
        actions.append({
            "type": "price_signal",
            "median": signals["price_anchor_median"],
            "reason": "median monthly worth — feed the retainer band",
        })
    for obj, n in signals.get("top_objections", [])[:2]:
        actions.append({
            "type": "objection_copy", "objection": obj, "mentions": n,
            "reason": "top hesitation → write objection-handling copy",
        })
    return actions


# ── Async boundaries ─────────────────────────────────────────────────────────

async def build_signals(db: AsyncSession, team_id: UUID) -> dict[str, Any]:
    """Pull every funnel source the lake exports and merge the signals."""
    since = datetime.now(UTC) - timedelta(days=30)
    responses = (
        await db.execute(
            select(SurveyResponse.answers)
            .where(SurveyResponse.team_id == team_id)
            .order_by(desc(SurveyResponse.submitted_at))
            .limit(200)
        )
    ).scalars().all()
    leads = (
        await db.execute(
            text(
                "SELECT source::text AS source, interest::text AS interest,"
                " company_size::text AS company_size"
                " FROM leads WHERE team_id=:t AND created_at >= :s"
            ),
            {"t": team_id, "s": since},
        )
    ).mappings().all()
    funnel_rows = (
        await db.execute(
            text(
                "SELECT payload->>'utm_source' AS platform,"
                " count(*) FILTER (WHERE event_name='page_view') AS views,"
                " count(*) FILTER (WHERE event_name LIKE '%\\_click') AS clicks,"
                " count(*) FILTER (WHERE event_name='contact_submit') AS leads"
                " FROM web_analytics_events"
                " WHERE payload ? 'utm_source' AND occurred_at >= :s"
                " GROUP BY 1"
            ),
            {"s": since},
        )
    ).mappings().all()
    ad_daily = (
        await db.execute(
            select(AdDailyMetric.campaign_name, AdDailyMetric.spend_eur,
                   AdDailyMetric.leads, AdDailyMetric.impressions)
            .where(AdDailyMetric.team_id == team_id, AdDailyMetric.day >= since.date())
        )
    ).mappings().all()

    signals = compute_survey_signals([{"answers": a} for a in responses])
    signals.update(compute_lead_signals([dict(r) for r in leads]))
    signals.update(compute_funnel_signals([dict(r) for r in funnel_rows]))
    signals.update(compute_ad_signals([
        {"campaign": r["campaign_name"], "spend_eur": r["spend_eur"],
         "leads": r["leads"], "impressions": r["impressions"]}
        for r in ad_daily
    ]))
    signals["team_id"] = str(team_id)
    signals["computed_at"] = datetime.now(UTC).isoformat()
    signals["actions"] = pick_actions(signals)
    return signals


async def apply_signals(db: AsyncSession, team_id: UUID) -> dict[str, Any]:
    """Compute signals, persist as an AnalyticsEvent, return the payload.

    The event feeds the next datalake export (``socialauto-insights`` table)
    and is the audit trail for why downstream actions fired.
    """
    signals = await build_signals(db, team_id)
    db.add(AnalyticsEvent(
        team_id=team_id,
        event_type="datalake_signals",
        platform="datalake",
        occurred_at=datetime.now(UTC),
        meta_data=signals,
    ))
    await db.commit()
    return signals


def render_slack_lines(signals: dict[str, Any]) -> list[str]:
    """Human-readable summary for the alert channel."""
    lines = [
        f"*datalake signals* — {signals.get('responses', 0)} survey resp · "
        f"{signals.get('lead_volume_30d', 0)} leads/30d"
    ]
    if signals.get("best_converting"):
        f = (signals.get("platform_funnels") or {}).get(signals["best_converting"], {})
        lines.append(
            f"• best funnel: {signals['best_converting']} "
            f"({f.get('views', 0)} views → {f.get('leads', 0)} leads)"
        )
    for src, n in (signals.get("lead_sources") or [])[:2]:
        lines.append(f"• leads from {src}: {n}")
    for camp, c in (signals.get("ad_campaigns") or {}).items():
        if c.get("cpl") is not None:
            lines.append(f"• ads: {camp} — €{c['spend']:.0f} spend, {c['leads']} leads, CPL €{c['cpl']:.0f}")
    if signals.get("audit_yes"):
        lines.append(f"• audit requests: {len(signals['audit_yes'])} → book them")
    for w, n in (signals.get("pain_topics") or [])[:3]:
        lines.append(f"• pain: {w} ({n}x) → content topic")
    if signals.get("price_anchor_median"):
        lines.append(f"• price anchor: €{signals['price_anchor_median']:.0f}/mo median")
    for o, n in (signals.get("top_objections") or [])[:2]:
        lines.append(f"• objection ({n}x): {o[:60]}")
    return lines

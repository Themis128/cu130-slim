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

from app.models.analytics import (
    AnalyticsEvent,
    FollowerSnapshot,
    PostAnalyticsSnapshot,
)
from app.models.email_campaign import SurveyResponse
from app.models.linkedin_ads import AdDailyMetric
from app.services.monetization import PLATFORM_PROGRAMS

# ── Signal extraction ────────────────────────────────────────────────────────

_STOPWORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "for",
    "to",
    "of",
    "in",
    "on",
    "is",
    "it",
    "my",
    "our",
    "we",
    "i",
    "that",
    "this",
    "with",
    "at",
    "be",
    "have",
    "has",
    "not",
    "no",
    "yes",
    "the",
    "ena",
    "kai",
    "to",
    "th",
    "thn",
    "gia",
    "se",
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
    for lead in leads:
        by_source[lead.get("source") or "unknown"] += 1
        if lead.get("interest"):
            by_interest[lead["interest"]] += 1
        if lead.get("company_size"):
            sizes[lead["company_size"]] += 1
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
    return {"platform_funnels": out, "best_converting": best[0] if best else None}


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


def compute_engagement_signals(rows: list[dict]) -> dict[str, Any]:
    """Post metrics → per-platform avg engagement rate + top post."""
    per: dict[str, list[float]] = {}
    top: dict[str, Any] = {"platform": None, "engagement_rate": 0.0}
    for r in rows:
        p = r.get("platform") or "?"
        per.setdefault(p, []).append(float(r.get("engagement_rate") or 0))
        if float(r.get("engagement_rate") or 0) > top["engagement_rate"]:
            top = {"platform": p, "engagement_rate": float(r["engagement_rate"]), "post_id": r.get("post_id")}
    return {
        "platform_engagement": {p: round(sum(v) / len(v), 4) for p, v in per.items()},
        "top_post": top if top["platform"] else None,
    }


def compute_growth_signals(snaps: list[dict]) -> dict[str, Any]:
    """Follower snapshots → per-platform velocity + monetization-gate ETA.

    snaps: rows of {platform, followers, captured_at} ordered by time —
    velocity uses first-vs-last per platform.
    """
    per: dict[str, list[tuple]] = {}
    for s in snaps:
        per.setdefault(s["platform"], []).append((s["captured_at"], s["followers"]))
    out: dict[str, Any] = {}
    for plat, pts in per.items():
        pts.sort(key=lambda x: x[0])
        if len(pts) < 2:
            out[plat] = {"followers": pts[-1][1], "per_week": None}
            continue
        (t0, f0), (t1, f1) = pts[0], pts[-1]
        days = max(1, (t1 - t0).days)
        per_week = round((f1 - f0) * 7.0 / days, 1)
        gate = None
        for metric, _label, thr, _src in (PLATFORM_PROGRAMS.get(plat) or {}).get("criteria", []):
            if metric == "followers" and isinstance(thr, int) and f1 < thr:
                gap = thr - f1
                eta = round(gap / (per_week / 7)) if per_week and per_week > 0 else None
                gate = {"threshold": thr, "gap": gap, "eta_days": eta}
        out[plat] = {"followers": f1, "per_week": per_week, "gate": gate}
    return {"growth": out}


def compute_email_signals(rows: list[dict]) -> dict[str, Any]:
    """Recipient status counts → per-kind send/open/response funnel.

    rows: {kind, status, n} — grouped recipient counts per campaign kind.
    """
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        kind = r.get("kind") or "marketing"
        d = out.setdefault(kind, {"sent": 0, "opened": 0, "clicked": 0, "responses": 0})
        n = int(r.get("n") or 0)
        status = r.get("status") or ""
        if status in ("sent", "delivered", "opened", "clicked"):
            d["sent"] += n
        if status == "opened":
            d["opened"] += n
        if status == "clicked":
            d["clicked"] += n
        if status == "responded":
            d["responses"] += n
    for d in out.values():
        if d.get("sent"):
            d["open_rate"] = round(d["opened"] / d["sent"], 3)
    return {"email_funnel": out}


def pick_actions(signals: dict[str, Any]) -> list[dict[str, Any]]:
    """Decide what the stack should do next — returns action dicts."""
    actions: list[dict[str, Any]] = []
    best = signals.get("best_converting")
    if best:
        actions.append(
            {
                "type": "platform_focus",
                "platform": best,
                "reason": f"{best} funnels the most leads — weight next posts there",
            }
        )
    for src, n in signals.get("lead_sources", [])[:2]:
        actions.append(
            {
                "type": "lead_source",
                "source": src,
                "volume": n,
                "reason": f"{src} produced {n} leads (30d) — keep feeding it",
            }
        )
    for camp, c in (signals.get("ad_campaigns") or {}).items():
        if c.get("cpl") is not None and c["cpl"] > 30:
            actions.append(
                {
                    "type": "ad_efficiency",
                    "campaign": camp,
                    "cpl": c["cpl"],
                    "reason": f"{camp} CPL €{c['cpl']:.0f} — over the €30 LinkedIn benchmark",
                }
            )
    for plat, g in (signals.get("growth") or {}).items():
        if g.get("gate") and g["gate"].get("eta_days"):
            actions.append(
                {
                    "type": "monetization_eta",
                    "platform": plat,
                    "gap": g["gate"]["gap"],
                    "eta_days": g["gate"]["eta_days"],
                    "reason": f"{plat} needs {g['gate']['gap']} more followers — ~{g['gate']['eta_days']}d at current pace",
                }
            )
    for plat, rate in (signals.get("platform_engagement") or {}).items():
        if rate > 0.05:
            actions.append(
                {
                    "type": "engagement_high",
                    "platform": plat,
                    "rate": rate,
                    "reason": f"{plat} at {rate:.1%} engagement — boost reach there",
                }
            )
    ef = signals.get("email_funnel") or {}
    for kind, d in ef.items():
        if d.get("sent") and d.get("open_rate", 0) < 0.2:
            actions.append(
                {
                    "type": "email_deliverability",
                    "kind": kind,
                    "open_rate": d["open_rate"],
                    "reason": f"{kind} open rate {d['open_rate']:.0%} — subject lines or list hygiene",
                }
            )
    for word, n in signals.get("pain_topics", [])[:3]:
        actions.append(
            {
                "type": "content_topic",
                "topic": word,
                "mentions": n,
                "reason": f"'{word}' showed up {n}x in survey pain answers",
            }
        )
    if signals.get("audit_yes"):
        actions.append(
            {
                "type": "audit_escalation",
                "emails": signals["audit_yes"],
                "reason": f"{len(signals['audit_yes'])} audit request(s) pending booking",
            }
        )
    if signals.get("price_anchor_median"):
        actions.append(
            {
                "type": "price_signal",
                "median": signals["price_anchor_median"],
                "reason": "median monthly worth — feed the retainer band",
            }
        )
    for obj, n in signals.get("top_objections", [])[:2]:
        actions.append(
            {
                "type": "objection_copy",
                "objection": obj,
                "mentions": n,
                "reason": "top hesitation → write objection-handling copy",
            }
        )
    if signals.get("coaching_latest"):
        cl = signals["coaching_latest"]
        actions.append(
            {
                "type": "coaching_prime",
                "topic": cl.get("topic", ""),
                "due": cl.get("homework_due", ""),
                "reason": "coaching action → map outcomes onto offer/survey surfaces",
            }
        )
    return actions


# ── Async boundaries ─────────────────────────────────────────────────────────


async def build_signals(db: AsyncSession, team_id: UUID) -> dict[str, Any]:
    """Pull every funnel source the lake exports and merge the signals."""
    since = datetime.now(UTC) - timedelta(days=30)
    responses = (
        (await db.execute(select(SurveyResponse.answers).where(SurveyResponse.team_id == team_id).order_by(desc(SurveyResponse.submitted_at)).limit(200)))
        .scalars()
        .all()
    )
    leads = (
        (
            await db.execute(
                text(
                    "SELECT source::text AS source, interest::text AS interest,"
                    " company_size::text AS company_size"
                    " FROM leads WHERE team_id=:t AND created_at >= :s"
                ),
                {"t": team_id, "s": since},
            )
        )
        .mappings()
        .all()
    )
    funnel_rows = (
        (
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
        )
        .mappings()
        .all()
    )
    ad_daily = (
        (
            await db.execute(
                select(AdDailyMetric.campaign_name, AdDailyMetric.spend_eur, AdDailyMetric.leads, AdDailyMetric.impressions).where(
                    AdDailyMetric.team_id == team_id, AdDailyMetric.day >= since.date()
                )
            )
        )
        .mappings()
        .all()
    )
    post_metrics = (
        (
            await db.execute(
                select(PostAnalyticsSnapshot.platform, PostAnalyticsSnapshot.engagement_rate, PostAnalyticsSnapshot.post_id).where(
                    PostAnalyticsSnapshot.team_id == team_id, PostAnalyticsSnapshot.captured_at >= since
                )
            )
        )
        .mappings()
        .all()
    )
    followers = (
        (
            await db.execute(
                select(FollowerSnapshot.platform, FollowerSnapshot.followers, FollowerSnapshot.captured_at)
                .where(FollowerSnapshot.team_id == team_id, FollowerSnapshot.captured_at >= since)
                .order_by(FollowerSnapshot.captured_at)
            )
        )
        .mappings()
        .all()
    )
    email_counts = (
        (
            await db.execute(
                text(
                    "SELECT c.kind::text AS kind, r.status::text AS status, count(*) AS n"
                    " FROM email_recipients r JOIN email_campaigns c ON c.id=r.campaign_id"
                    " WHERE r.team_id=:t GROUP BY 1,2"
                ),
                {"t": team_id},
            )
        )
        .mappings()
        .all()
    )

    coaching = (
        (
            await db.execute(
                text(
                    "SELECT occurred_at, meta_data FROM analytics_events WHERE team_id=:t AND event_type='coaching_action' ORDER BY occurred_at DESC LIMIT 10"
                ),
                {"t": team_id},
            )
        )
        .mappings()
        .all()
    )
    signals = compute_survey_signals([{"answers": a} for a in responses])
    signals.update(compute_lead_signals([dict(r) for r in leads]))
    signals.update(compute_funnel_signals([dict(r) for r in funnel_rows]))
    signals.update(compute_engagement_signals([dict(r) for r in post_metrics]))
    signals.update(compute_growth_signals([dict(r) for r in followers]))
    signals.update(compute_email_signals([dict(r) for r in email_counts]))
    signals.update(
        compute_ad_signals(
            [{"campaign": r["campaign_name"], "spend_eur": r["spend_eur"], "leads": r["leads"], "impressions": r["impressions"]} for r in ad_daily]
        )
    )
    if coaching:
        latest = coaching[0]
        signals["coaching"] = [{"occurred_at": str(r["occurred_at"]), **(r["meta_data"] or {})} for r in coaching]
        signals["coaching_latest"] = dict(latest["meta_data"] or {})
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
    db.add(
        AnalyticsEvent(
            team_id=team_id,
            event_type="datalake_signals",
            platform="datalake",
            occurred_at=datetime.now(UTC),
            meta_data=signals,
        )
    )
    await db.commit()
    return signals


def render_slack_lines(signals: dict[str, Any]) -> list[str]:
    """Human-readable summary for the alert channel."""
    lines = [f"*datalake signals* — {signals.get('responses', 0)} survey resp · {signals.get('lead_volume_30d', 0)} leads/30d"]
    if signals.get("best_converting"):
        f = (signals.get("platform_funnels") or {}).get(signals["best_converting"], {})
        lines.append(f"• best funnel: {signals['best_converting']} ({f.get('views', 0)} views → {f.get('leads', 0)} leads)")
    for src, n in (signals.get("lead_sources") or [])[:2]:
        lines.append(f"• leads from {src}: {n}")
    for camp, c in (signals.get("ad_campaigns") or {}).items():
        if c.get("cpl") is not None:
            lines.append(f"• ads: {camp} — €{c['spend']:.0f} spend, {c['leads']} leads, CPL €{c['cpl']:.0f}")
    for plat, g in (signals.get("growth") or {}).items():
        if g.get("gate"):
            eta = f" (~{g['gate']['eta_days']}d)" if g["gate"].get("eta_days") else ""
            lines.append(f"• {plat}: {g['followers']} followers, +{g.get('per_week') or 0}/wk — gate gap {g['gate']['gap']}{eta}")
    for plat, rate in (signals.get("platform_engagement") or {}).items():
        lines.append(f"• {plat} engagement: {rate:.1%}")
    for kind, d in (signals.get("email_funnel") or {}).items():
        if d.get("sent"):
            lines.append(f"• email {kind}: {d['sent']} sent, open {d.get('open_rate', 0):.0%}")
    if signals.get("audit_yes"):
        lines.append(f"• audit requests: {len(signals['audit_yes'])} → book them")
    for w, n in (signals.get("pain_topics") or [])[:3]:
        lines.append(f"• pain: {w} ({n}x) → content topic")
    if signals.get("price_anchor_median"):
        lines.append(f"• price anchor: €{signals['price_anchor_median']:.0f}/mo median")
    if signals.get("coaching_latest"):
        cl = signals["coaching_latest"]
        lines.append(f"• coaching: {cl.get('topic', '')} — due {cl.get('homework_due', '')}")
    for o, n in (signals.get("top_objections") or [])[:2]:
        lines.append(f"• objection ({n}x): {o[:60]}")
    return lines


async def funnel_answer(question: str) -> dict[str, Any]:
    """Genie-style funnel Q&A — pulls the latest MinIO funnel-zone exports
    and answers a natural-language question over them via the DMR.
    The compute_* signals are the metrics surface; this is the
    plain-English front door.  Degrades to an error dict when the DMR
    or the zone is unavailable."""
    from app.services import ai_functions, minio_storage

    if not minio_storage.minio_enabled():
        return {"answer": None, "error": "datalake offline"}
    zone = ""
    # the structured summary carries counts + statuses — the model answers
    # over it without streaming 200 rows.
    try:
        zone = (await minio_storage.get_object(
            "funnel/summary.json")).decode() + "\n"
    except Exception:
        pass
    for name in ("leads", "contacts", "segments"):
        key = f"funnel/{name}/latest.jsonl"
        try:
            zone += (await minio_storage.get_object(key)).decode() + "\n"
        except Exception:
            pass
    if not zone.strip():
        return {"answer": None, "error": "funnel zone empty"}
    answer = await ai_functions.ai_extract(
        f"the direct answer to: {question}", zone[:8000])
    return {"answer": None if answer == "NONE" else answer,
            "question": question, "rows_seen": zone.count("\n")}

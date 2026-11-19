"""Funnel reports — data-driven decision reports for posts, media and trends.

Four scheduled reports closing the gaps the daily batch didn't cover:

- ``lead_pulse``     (daily)  — new leads/bookings/unanswered DMs in 24h.
- ``attribution``    (daily)  — UTM/referrer → consultation → lead join, 7d.
- ``content_weekly`` (weekly) — top/flop posts vs baseline, follower deltas.
- ``funnel_monthly`` (monthly)— lead→opportunity→won economics + spend.

Design rule (from the reporting-cadence research): daily reports are
operational scans and anomaly flags; content decisions live in the weekly
review against 7–30d baselines. Every section ends in an action line.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from sqlalchemy import String, cast, func, select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.analytics import FollowerSnapshot, PostAnalyticsSnapshot
from app.models.content import Post
from app.models.lead import Lead
from app.models.linkedin_ads import AdDailyMetric
from app.models.social_account import SocialAccount
from app.models.web_analytics import WebAnalyticsEvent

logger = logging.getLogger(__name__)

ATHENS = ZoneInfo("Europe/Athens")


@dataclass
class FunnelReport:
    key: str
    title: str
    subject: str
    preheader: str
    sections: list[tuple[str, list[str]]] = field(default_factory=list)
    emailed: bool = False
    email_error: str | None = None

    def line(self, section: str, *texts: str) -> None:
        for title, lines in self.sections:
            if title == section:
                lines.extend(texts)
                return
        self.sections.append((section, list(texts)))

    def to_text(self) -> str:
        out = [self.title, "=" * len(self.title), ""]
        for title, lines in self.sections:
            out.append(title.upper())
            out.append("-" * len(title))
            out.extend(lines or ["(none)"])
            out.append("")
        return "\n".join(out).rstrip() + "\n"

    def to_html(self) -> str:
        from app.services.email_brand import render_branded_html

        return render_branded_html(
            self.to_text(),
            subject=self.subject,
            from_name="Cloudless Ops",
            preheader=self.preheader,
        )


async def _send(report: FunnelReport, *, email_to: str = "") -> FunnelReport:
    from app.services.email_digest import send_email

    settings = get_settings()
    to = (email_to or getattr(settings, "DIGEST_EMAIL_TO", "") or "").strip()
    if not to:
        report.email_error = "DIGEST_EMAIL_TO not set"
        return report
    try:
        await send_email(
            subject=report.subject,
            text_body=report.to_text(),
            html_body=report.to_html(),
            to_addrs=[a.strip() for a in to.split(",") if a.strip()],
            category="ops-alert",
        )
        report.emailed = True
    except Exception as exc:  # noqa: BLE001
        report.email_error = str(exc) or repr(exc)
        logger.exception("Failed to email %s", report.key)
    return report


# ── shared helpers ──────────────────────────────────────────────────────────


def _since_hours(hours: int) -> datetime:
    return datetime.now(UTC) - timedelta(hours=hours)


def _referrer_host(ref: str | None) -> str:
    if not ref:
        return "(direct)"
    try:
        host = urlparse(ref).netloc.lower()
        return host[4:] if host.startswith("www.") else host or "(direct)"
    except Exception:  # noqa: BLE001
        return "(direct)"


async def _espo_count(
    entity: str, *, since: datetime, group_attr: str = "source", select_attrs: list[str] | None = None, extra_where: list[dict[str, Any]] | None = None
) -> dict[str, int]:
    """Count EspoCRM entities created since ``since``, grouped by an attr."""
    from app.services import espocrm_client as espo

    if not espo.configured():
        return {}
    where = [
        {"type": "greaterThan", "attribute": "createdAt", "value": since.astimezone(UTC).isoformat()},
        *(extra_where or []),
    ]
    try:
        items = await espo.list_all(
            entity,
            select=select_attrs or ["id", group_attr],
            where=where,
            max_items=2000,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("espo %s query failed: %s", entity, exc)
        return {"(query failed)": -1}
    counts: dict[str, int] = {}
    for it in items:
        key = str(it.get(group_attr) or "(unset)")
        counts[key] = counts.get(key, 0) + 1
    return counts


async def _social_leads_by_source(since: datetime, until: datetime | None = None) -> dict[str, int]:
    cond = [Lead.created_at >= since]
    if until is not None:
        cond.append(Lead.created_at < until)
    async with async_session_maker() as db:
        rows = (await db.execute(select(Lead.source, func.count()).where(*cond).group_by(Lead.source))).all()
    out: dict[str, int] = {}
    for r in rows:
        src = r[0]
        key = src.value if hasattr(src, "value") else str(src)
        out[key] = out.get(key, 0) + int(r[1])
    return out


async def _inbox_pulse() -> tuple[dict[str, dict[str, int]], list[dict[str, str]], dict[str, Any]]:
    """Live-capture the unified inbox for every team (also persists a snapshot
    batch for history), then summarize unread threads + WhatsApp FRT stats.

    Returns (by_platform, unread_conversations, frt_stats).
    """
    from app.models.user import Team
    from app.services.inbox_snapshot import capture, latest_summary, whatsapp_frt_stats

    by_platform: dict[str, dict[str, int]] = {}
    unread: list[dict[str, str]] = []
    frt: dict[str, Any] = {}
    async with async_session_maker() as db:
        team_ids = (await db.execute(select(Team.id))).scalars().all()
        for tid in team_ids:
            try:
                await capture(db, tid)
                summary = await latest_summary(db, tid)
            except Exception as exc:  # noqa: BLE001 — a wedged platform must not kill the report
                logger.warning("inbox snapshot failed for team %s: %s", tid, exc)
                continue
            for plat, counts in summary.by_platform.items():
                agg = by_platform.setdefault(plat, {"total": 0, "unread": 0})
                agg["total"] += counts.get("total", 0)
                agg["unread"] += counts.get("unread", 0)
            unread.extend(summary.unread_conversations)
        try:
            frt = await whatsapp_frt_stats(db, since=datetime.now(UTC) - timedelta(days=7))
        except Exception as exc:  # noqa: BLE001
            logger.warning("whatsapp FRT stats failed: %s", exc)
            frt = {"error": str(exc)}
    return by_platform, unread, frt


def _fmt_counts(counts: dict[str, int], empty: str = "none") -> list[str]:
    if not counts:
        return [empty]
    return [f"• {k}: {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])]


# ── 1. daily lead & inbox pulse ─────────────────────────────────────────────


async def build_lead_pulse() -> FunnelReport:
    since = _since_hours(24)
    report = FunnelReport(
        key="lead_pulse",
        title="Daily Lead & Inbox Pulse",
        subject="[Cloudless] Lead pulse — {d}",
        preheader="New leads, bookings and unanswered DMs in the last 24h.",
    )
    report.subject = report.subject.format(d=datetime.now(ATHENS).date().isoformat())

    sa_leads = await _social_leads_by_source(since)
    report.line("SocialAuto leads (24h)", *_fmt_counts(sa_leads))

    espo_leads = await _espo_count("Lead", since=since)
    report.line("EspoCRM leads (24h)", *_fmt_counts(espo_leads))

    espo_opps = await _espo_count("Opportunity", since=since, group_attr="stage")
    report.line("Bookings / opportunities created (24h)", *_fmt_counts(espo_opps))

    # Unified inbox snapshot (all platforms) + WhatsApp reply-time stats
    inbox_platforms, unread_convos, frt = await _inbox_pulse()
    report.line(
        "Unified inbox (live capture)",
        *[f"• {plat}: {c.get('total', 0)} threads, {c.get('unread', 0)} unread" for plat, c in sorted(inbox_platforms.items())]
        or ["no conversations / all fetches failed"],
    )
    if unread_convos:
        report.line("Unread conversations needing reply", *[f"• [{u['platform']}] {u['sender']}: {u['preview']}" for u in unread_convos[:10]])
    if frt.get("error"):
        report.line("WhatsApp response time (7d)", f"• stats unavailable: {frt['error']}")
    elif frt.get("replies_measured"):
        report.line(
            "WhatsApp response time (7d)",
            f"• {frt['replies_measured']} replies measured — median {frt.get('median_frt')}, p90 {frt.get('p90_frt')}, slowest {frt.get('slowest_frt')}",
            f"• within 4h target: {frt.get('within_target')}, over 24h: {frt.get('breaches_24h')}",
        )
        for u in frt.get("unanswered", []):
            report.line("WhatsApp unanswered >2h", f"• {u.get('name', u.get('phone'))} — waiting {u.get('waiting_hours')}")
    else:
        report.line("WhatsApp response time (7d)", "• no inbound messages in window")

    # consultation intent on the site
    utm_src = WebAnalyticsEvent.payload["utm_source"].astext
    async with async_session_maker() as db:
        consult = (
            await db.execute(
                select(utm_src, func.count()).where(WebAnalyticsEvent.occurred_at >= since, WebAnalyticsEvent.path.like("%consultation%")).group_by(utm_src)
            )
        ).all()
    report.line("Consultation page visits (24h, by utm_source)", *([f"• {r[0] or '(untagged)'}: {r[1]}" for r in consult] or ["none recorded"]))

    total = sum(v for v in sa_leads.values()) + sum(v for k, v in espo_leads.items() if v > 0)
    total_unread = sum(c.get("unread", 0) for c in inbox_platforms.values())
    report.line(
        "Takeaway",
        f"• {total} new leads + {sum(max(v, 0) for v in espo_opps.values())} opportunities in 24h"
        + (f" — {total_unread} unread conversations across {len(inbox_platforms)} channels" if total_unread else ""),
    )
    return report


# ── 2. daily attribution digest ─────────────────────────────────────────────


async def build_attribution() -> FunnelReport:
    since = datetime.now(UTC) - timedelta(days=7)
    report = FunnelReport(
        key="attribution",
        title="Weekly Traffic → Lead Attribution",
        subject="[Cloudless] Attribution digest — {d}",
        preheader="Which channels actually drive consultation visits and leads.",
    )
    report.subject = report.subject.format(d=datetime.now(ATHENS).date().isoformat())

    src_expr = func.coalesce(
        WebAnalyticsEvent.payload["utm_source"].astext,
        WebAnalyticsEvent.referrer,
    )
    async with async_session_maker() as db:
        # site traffic by utm_source (payload) falling back to referrer host
        rows = (
            await db.execute(
                select(src_expr, func.count())
                .where(WebAnalyticsEvent.occurred_at >= since, WebAnalyticsEvent.event_name == "page_view")
                .group_by(src_expr)
                .order_by(func.count().desc())
                .limit(10)
            )
        ).all()
    counts: dict[str, int] = {}
    for raw, n in rows:
        s = str(raw) if raw else "(direct)"
        key = _referrer_host(s) if s.startswith(("http", "www.")) else s
        counts[key] = counts.get(key, 0) + int(n)
    report.line("Site visits by source (7d)", *_fmt_counts(counts))

    consult_expr = func.coalesce(
        WebAnalyticsEvent.payload["utm_source"].astext,
        WebAnalyticsEvent.payload["utm_campaign"].astext,
        WebAnalyticsEvent.referrer,
    )
    async with async_session_maker() as db:
        consult = (
            await db.execute(
                select(consult_expr, func.count())
                .where(WebAnalyticsEvent.occurred_at >= since, WebAnalyticsEvent.path.like("%consultation%"))
                .group_by(consult_expr)
            )
        ).all()
    ccounts: dict[str, int] = {}
    for raw, n in consult:
        key = str(raw) if raw else "(untagged)"
        if key.startswith(("http", "www.")):
            key = _referrer_host(key)
        ccounts[key] = ccounts.get(key, 0) + int(n)
    report.line("Consultation visits by source (7d)", *_fmt_counts(ccounts))

    # bookings with first-touch attribution (written by the calcom webhook
    # from the metadata the site attaches at booking time)
    booking_src = WebAnalyticsEvent.payload["utm_source"].astext
    async with async_session_maker() as db:
        bookings = (
            await db.execute(
                select(booking_src, func.count())
                .where(WebAnalyticsEvent.occurred_at >= since, WebAnalyticsEvent.event_name == "booking_created")
                .group_by(booking_src)
            )
        ).all()
    bcounts: dict[str, int] = {}
    for raw, n in bookings:
        bcounts[str(raw) if raw else "(unattributed)"] = bcounts.get(str(raw) if raw else "(unattributed)", 0) + int(n)
    report.line("Bookings by source (7d)", *_fmt_counts(bcounts))

    sa = await _social_leads_by_source(since)
    report.line("SocialAuto leads by channel (7d)", *_fmt_counts(sa))
    espo = await _espo_count("Lead", since=since)
    report.line("EspoCRM leads by source (7d)", *_fmt_counts(espo))
    opps = await _espo_count("Opportunity", since=since, group_attr="stage")
    report.line("Opportunities by stage (7d)", *_fmt_counts(opps))

    booked = sum(v for k, v in opps.items() if v > 0)
    total_bookings = sum(bcounts.values())
    report.line(
        "Takeaway",
        f"• {total_bookings} attributed bookings, {booked} opportunities from "
        f"{sum(v for v in sa.values()) + sum(max(v, 0) for v in espo.values())} leads this week"
        + (" — top consult source: " + max(ccounts, key=ccounts.get) if ccounts else ""),
    )
    return report


# ── 3. weekly top/flop content review ───────────────────────────────────────


async def build_content_weekly() -> FunnelReport:
    now = datetime.now(UTC)
    wk_start = now - timedelta(days=7)
    prev_start = now - timedelta(days=14)
    report = FunnelReport(
        key="content_weekly",
        title="Weekly Content Scoreboard",
        subject="[Cloudless] Content scoreboard — week of {d}",
        preheader="Top and flop posts vs baseline, follower movement.",
    )
    report.subject = report.subject.format(d=(now - timedelta(days=7)).date().isoformat())

    async with async_session_maker() as db:
        # Latest snapshot per publication — keyed by
        # (account, platform, platform_post_id|post_id) so account-level
        # aggregate rows (no post/publication id) can't collide or drop
        # published targets that only exist platform-side.
        pub_key = func.coalesce(
            PostAnalyticsSnapshot.platform_post_id,
            cast(PostAnalyticsSnapshot.post_id, String),
        )

        def _snap_window(start, end):
            sub = (
                select(
                    PostAnalyticsSnapshot.social_account_id,
                    PostAnalyticsSnapshot.platform,
                    pub_key.label("pub"),
                    func.max(PostAnalyticsSnapshot.captured_at).label("mx"),
                )
                .where(
                    PostAnalyticsSnapshot.captured_at >= start,
                    PostAnalyticsSnapshot.captured_at < end,
                    (PostAnalyticsSnapshot.post_id.isnot(None)) | (PostAnalyticsSnapshot.platform_post_id.isnot(None)),
                )
                .group_by(
                    PostAnalyticsSnapshot.social_account_id,
                    PostAnalyticsSnapshot.platform,
                    pub_key,
                )
                .subquery()
            )
            return (
                select(
                    PostAnalyticsSnapshot.social_account_id,
                    PostAnalyticsSnapshot.platform,
                    pub_key.label("pub"),
                    PostAnalyticsSnapshot.post_id,
                    PostAnalyticsSnapshot.impressions,
                    PostAnalyticsSnapshot.engagement,
                    PostAnalyticsSnapshot.reach,
                    PostAnalyticsSnapshot.engagement_rate,
                    Post.content_text,
                )
                .join(
                    sub,
                    (PostAnalyticsSnapshot.social_account_id == sub.c.social_account_id)
                    & (PostAnalyticsSnapshot.platform == sub.c.platform)
                    & (pub_key == sub.c.pub)
                    & (PostAnalyticsSnapshot.captured_at == sub.c.mx),
                )
                .outerjoin(Post, Post.id == PostAnalyticsSnapshot.post_id)
            )

        cur = (await db.execute(_snap_window(wk_start, now))).all()
        prev = (await db.execute(_snap_window(prev_start, wk_start))).all()

    def _key(r):
        return (str(r[0]), r[1], str(r[2]))

    prev_map = {_key(r): r for r in prev}
    rows: list[dict[str, Any]] = []
    plat: dict[str, list[int]] = {}
    for r in cur:
        p = prev_map.get(_key(r))
        prev_eng = int(p[5]) if p else 0
        totals = plat.setdefault(str(r[1]), [0, 0])
        totals[0] += int(r[5] or 0)
        totals[1] += int(r[4] or 0)
        rows.append(
            {
                "post_id": str(r[3] or r[2])[:8],
                "platform": r[1],
                "impressions": int(r[4] or 0),
                "engagement": int(r[5] or 0),
                "reach": int(r[6] or 0),
                "er": float(r[7] or 0),
                "prev_eng": prev_eng,
                "text": (r[8] or "")[:70].replace("\n", " "),
            }
        )

    # top/flop by engagement among posts with meaningful reach
    meaningful = [r for r in rows if r["impressions"] >= 50]
    top = sorted(meaningful, key=lambda r: -r["engagement"])[:5]
    flop = sorted(meaningful, key=lambda r: r["engagement"])[:3] if meaningful else []

    def _fmt_post(r: dict) -> str:
        delta = f", prev-wk {r['prev_eng']}" if r["prev_eng"] else ""
        return f"• [{r['platform']}] {r['text'] or '(no text)'} — {r['engagement']} eng / {r['impressions']} imp / ER {r['er']:.1f}%{delta}"

    report.line("Top posts (7d, min 50 impressions)", *[_fmt_post(r) for r in top] or ["not enough snapshot data yet"])
    report.line("Flop posts (7d)", *[_fmt_post(r) for r in flop] or ["none"])

    # per-platform totals this week
    report.line("Per-platform totals (7d)", *[f"• {k}: {v[0]} eng / {v[1]} imp" for k, v in sorted(plat.items())] or ["none"])

    # follower movement
    async with async_session_maker() as db:
        latest = (
            select(
                FollowerSnapshot.social_account_id,
                SocialAccount.platform,
                SocialAccount.username,
                func.max(FollowerSnapshot.captured_at).label("mx"),
            )
            .join(SocialAccount, SocialAccount.id == FollowerSnapshot.social_account_id)
            .group_by(FollowerSnapshot.social_account_id, SocialAccount.platform, SocialAccount.username)
            .subquery()
        )
        snaps = (
            await db.execute(
                select(FollowerSnapshot.social_account_id, FollowerSnapshot.followers, SocialAccount.platform, SocialAccount.username)
                .join(latest, (FollowerSnapshot.social_account_id == latest.c.social_account_id) & (FollowerSnapshot.captured_at == latest.c.mx))
                .join(SocialAccount, SocialAccount.id == FollowerSnapshot.social_account_id)
            )
        ).all()
        old = (
            await db.execute(
                select(FollowerSnapshot.social_account_id, FollowerSnapshot.followers)
                .where(FollowerSnapshot.captured_at <= wk_start, FollowerSnapshot.captured_at >= prev_start)
                .order_by(FollowerSnapshot.captured_at.desc())
            )
        ).all()
    old_map: dict[Any, int] = {}
    for sid, f in old:
        old_map.setdefault(sid, int(f))
    deltas = []
    for sid, followers, platform, username in snaps:
        prev_f = old_map.get(sid)
        if prev_f is None:
            continue
        d = int(followers) - prev_f
        if d:
            deltas.append(f"• {platform} @{username or sid}: {d:+d} → {followers:,}")
    report.line("Follower movement (7d)", *(deltas[:12] or ["no change / no baseline"]))

    report.line("Takeaway", "• double down on the top format/topic; review flop hooks before reposting similar angles")
    return report


# ── 4. monthly funnel economics ─────────────────────────────────────────────


async def build_funnel_monthly() -> FunnelReport:
    # Report the COMPLETED month (Athens calendar) — the beat fires on the
    # 1st, so "this month" would be an empty window.
    now_local = datetime.now(ATHENS)
    cur_month_local = now_local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_start_local = (cur_month_local - timedelta(days=1)).replace(day=1)
    prev_start_local = (month_start_local - timedelta(days=1)).replace(day=1)
    month_start = month_start_local.astimezone(UTC)
    month_end = cur_month_local.astimezone(UTC)
    prev_start = prev_start_local.astimezone(UTC)
    label = month_start_local.strftime("%Y-%m")
    report = FunnelReport(
        key="funnel_monthly",
        title=f"Monthly Funnel Economics — {label}",
        subject=f"[Cloudless] Funnel economics — {label}",
        preheader="Lead → opportunity → won economics and spend for the month.",
    )

    # EspoCRM pipeline
    month_range = [{"type": "lessThan", "attribute": "createdAt", "value": month_end.isoformat()}]
    leads_m = await _espo_count("Lead", since=month_start, extra_where=month_range)
    leads_prev = await _espo_count("Lead", since=prev_start, extra_where=[{"type": "lessThan", "attribute": "createdAt", "value": month_start.isoformat()}])
    report.line(f"EspoCRM leads ({label})", *_fmt_counts(leads_m), f"— prev month total: {sum(max(v, 0) for v in leads_prev.values())}")

    opps = await _espo_count("Opportunity", since=month_start, group_attr="stage", extra_where=month_range)
    won = sum(v for k, v in opps.items() if "won" in k.lower() and "lost" not in k.lower())
    report.line("Opportunities by stage", *_fmt_counts(opps), f"— Closed Won this month: {won}")

    # SocialAuto-side leads
    sa = await _social_leads_by_source(month_start, until=month_end)
    report.line("SocialAuto leads by channel", *_fmt_counts(sa))

    # Polar revenue
    try:
        from app.services import polar_api

        if polar_api.polar_configured():
            orders = await polar_api._request(
                "GET",
                "/orders",
                params={
                    "limit": 100,
                    "created_at__gte": month_start.isoformat(),
                    "created_at__lte": month_end.isoformat(),
                },
            )
            items = orders.get("items") or orders.get("data") or []
            paid = [o for o in items if isinstance(o, dict) and o.get("status") in (None, "paid")]
            # Polar amounts are in the currency's smallest unit (cents).
            total = sum(float(o.get("total_amount") or 0) / 100.0 for o in paid)
            currency = next((o.get("currency") for o in paid if o.get("currency")), "eur")
            report.line("Polar orders (month)", f"• {len(paid)} orders, ~{total:.2f} {str(currency).upper()}")
        else:
            report.line("Polar orders (month)", "• Polar not configured")
    except Exception as exc:  # noqa: BLE001
        report.line("Polar orders (month)", f"• query failed: {exc}")

    # LinkedIn ads spend
    async with async_session_maker() as db:
        spend = (
            await db.execute(
                select(
                    func.sum(AdDailyMetric.spend_eur), func.sum(AdDailyMetric.clicks), func.sum(AdDailyMetric.impressions), func.sum(AdDailyMetric.leads)
                ).where(
                    AdDailyMetric.day >= month_start_local.date(),
                    AdDailyMetric.day < cur_month_local.date(),
                )
            )
        ).one()
    report.line(
        "LinkedIn ads (month)",
        f"• spend €{float(spend[0] or 0):.2f} · {int(spend[1] or 0)} clicks · {int(spend[2] or 0)} impressions · {int(spend[3] or 0)} leads",
    )

    report.line(
        "Takeaway", f"• funnel: {sum(max(v, 0) for v in leads_m.values())} CRM leads → {sum(max(v, 0) for v in opps.values())} opportunities → {won} won"
    )
    return report


BUILDERS = {
    "lead_pulse": build_lead_pulse,
    "attribution": build_attribution,
    "content_weekly": build_content_weekly,
    "funnel_monthly": build_funnel_monthly,
}

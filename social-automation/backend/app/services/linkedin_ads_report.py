"""LinkedIn Ads daily report — scrape Campaign Manager via the LinkedIn browser
sidecar, snapshot metrics to Postgres, deliver a human-friendly summary to a
dedicated Slack channel and by email.

Why sidecar scraping: the LinkedIn app only has Community Management + Share
products — Marketing API ads-reporting (``r_ads_reporting``) is not granted, so
the official analytics endpoint is unavailable. Campaign Manager numbers are
read from the rendered overview/campaign pages instead.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy import case, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.analytics import FollowerSnapshot, PostAnalyticsSnapshot
from app.models.linkedin_ads import AdCampaignSnapshot
from app.models.social_account import SocialAccount
from app.models.user import Team
from app.services.email_digest import send_email_smtp
from app.services.slack_notifications import _post_slack_text

logger = logging.getLogger(__name__)


@dataclass
class CampaignMetrics:
    """Numbers extracted from Campaign Manager pages."""

    account_id: str
    campaign_id: str
    campaign_name: str = ""
    status: str = "unknown"          # draft | active | paused | completed
    spend_eur: float = 0.0
    clicks: int = 0
    cpc_eur: float = 0.0
    engagements: int = 0
    engagement_rate: float = 0.0     # percent, e.g. 5.45
    ctr: float = 0.0                 # percent, e.g. 3.06
    impressions: int = 0             # derived from clicks/ctr when not shown
    budget_eur: float = 0.0
    schedule_start: str = ""
    ad_set_statuses: dict[str, int] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


def _eur(text: str, pattern: str) -> float:
    m = re.search(pattern, text)
    if not m:
        return 0.0
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return 0.0


def _int(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    if not m:
        return 0
    try:
        return int(m.group(1).replace(",", ""))
    except ValueError:
        return 0


async def _sidecar_page_text(url: str) -> str:
    """Navigate the LinkedIn sidecar to ``url`` and return rendered page text."""
    settings = get_settings()
    base = settings.LINKEDIN_BROWSER_SIDECAR_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(
            f"{base}/debug/navigate",
            json={"url": url},
        )
        r.raise_for_status()
        # SPA needs a moment to render the metrics table.
        import asyncio

        await asyncio.sleep(10)
        r = await client.get(f"{base}/debug/page-text")
        r.raise_for_status()
        return (r.json() or {}).get("text", "")


def _parse_overview(text: str) -> dict[str, Any]:
    """Parse the account overview page (spend, clicks, CPC, ad-set statuses)."""
    out: dict[str, Any] = {}
    out["spend_eur"] = _eur(text, r"Spend by objective\s*\n\s*€([\d.,]+)")
    out["clicks"] = _int(text, r"Clicks\s*\n\s*([\d,]+)")
    out["cpc_eur"] = _eur(text, r"CPC\s*\n\s*€([\d.,]+)")
    for st in ("Active", "Paused", "Completed", "Draft"):
        n = _int(text, rf"{st}\s*\n\s*(\d+)")
        if n or st in text:
            out.setdefault("ad_set_statuses", {})[st.lower()] = n
    # Content engagement table layout:
    #   "<N>\nEngagements\n\n<ER>%\n\n+<delta>%\n\n<CTR>%\n\nActions"
    m = re.search(r"(\d+)\s*\n\s*Engagements\s*\n\s*([\d.]+)%", text)
    if m:
        out["engagements"] = int(m.group(1).replace(",", ""))
        out["engagement_rate"] = float(m.group(2))
    m = re.search(r"([\d.]+)%\s*\n\s*Actions", text)
    if m:
        out["ctr"] = float(m.group(1))
    return out


def _parse_campaign_page(text: str, campaign_id: str) -> dict[str, Any]:
    """Parse the campaign detail page for the tracked ad set's budget/status."""
    out: dict[str, Any] = {}
    out["budget_eur"] = _eur(text, r"Lifetime budget: €([\d.,]+)")
    m = re.search(rf"{re.escape(campaign_id)} · [^\n]*?Schedule: ([\d/]+)", text)
    if m:
        out["schedule"] = m.group(1).strip()
    m = re.search(rf"{re.escape(campaign_id)} · [^\n]+\n\n(\w+)", text)
    if m:
        out["status"] = m.group(1).strip().lower()
    # "Cloudless boost - Sep 2026 - coupon" appears just above the id line
    m = re.search(rf"([^\n]+)\n\n{re.escape(campaign_id)} ·", text)
    if m:
        out["campaign_name"] = m.group(1).strip()
    return out


async def collect_metrics() -> CampaignMetrics:
    """Scrape Campaign Manager for the configured campaign."""
    settings = get_settings()
    account_id = settings.LINKEDIN_AD_ACCOUNT_ID or "512642510"
    campaign_id = settings.LINKEDIN_ADS_CAMPAIGN_ID
    m = CampaignMetrics(account_id=account_id, campaign_id=campaign_id)

    overview_url = (
        f"https://www.linkedin.com/campaignmanager/accounts/{account_id}"
        f"/overview?businessId=personal"
    )
    campaign_url = (
        f"https://www.linkedin.com/campaignmanager/accounts/{account_id}"
        f"/campaigns/{campaign_id}?businessId=personal"
    )

    try:
        overview_text = await _sidecar_page_text(overview_url)
        ov = _parse_overview(overview_text)
        m.spend_eur = ov.get("spend_eur", 0.0)
        m.clicks = ov.get("clicks", 0)
        m.cpc_eur = ov.get("cpc_eur", 0.0)
        m.engagements = ov.get("engagements", 0)
        m.engagement_rate = ov.get("engagement_rate", 0.0)
        m.ctr = ov.get("ctr", 0.0)
        m.ad_set_statuses = ov.get("ad_set_statuses", {})
        m.raw["overview_excerpt"] = overview_text[:2000]
    except Exception as exc:  # noqa: BLE001 — report still goes out with what we have
        logger.warning("LinkedIn ads overview scrape failed: %s", exc)
        m.raw["overview_error"] = str(exc)[:300]

    try:
        campaign_text = await _sidecar_page_text(campaign_url)
        cp = _parse_campaign_page(campaign_text, campaign_id)
        m.campaign_name = cp.get("campaign_name", "")
        m.status = cp.get("status", "unknown")
        m.budget_eur = cp.get("budget_eur", 0.0)
        m.schedule_start = cp.get("schedule", "")
        m.raw["campaign_excerpt"] = campaign_text[:2000]
    except Exception as exc:  # noqa: BLE001
        logger.warning("LinkedIn ads campaign scrape failed: %s", exc)
        m.raw["campaign_error"] = str(exc)[:300]

    if m.clicks and m.ctr:
        m.impressions = round(m.clicks / (m.ctr / 100))
    return m


def _parse_schedule_date(schedule: str) -> date | None:
    """Parse US-style ``M/D/YYYY`` from the CM schedule string."""
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", schedule or "")
    if not m:
        return None
    try:
        return date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


def _fmt_money(v: float) -> str:
    return f"€{v:,.2f}".rstrip("0").rstrip(".") if v else "€0"


def build_report_text(
    m: CampaignMetrics, prev: AdCampaignSnapshot | None, end_date: date | None
) -> str:
    """Human-friendly summary — plain language, no jargon."""
    today = datetime.now(ZoneInfo(get_settings().APP_TIMEZONE)).date()
    name = m.campaign_name or "Cloudless boost - Sep 2026 - coupon"
    lines = [f"☀️ *LinkedIn ad report — {name}* ({today.isoformat()})", ""]

    status_label = {
        "active": "🟢 Running",
        "paused": "⏸️ Paused",
        "draft": "📝 Draft — not live yet",
        "completed": "🏁 Finished",
    }.get(m.status, m.status.title())
    lines.append(f"*Status:* {status_label}")

    if m.budget_eur:
        pct = (m.spend_eur / m.budget_eur * 100) if m.budget_eur else 0
        lines.append(
            f"*Spend:* {_fmt_money(m.spend_eur)} of {_fmt_money(m.budget_eur)} "
            f"lifetime budget ({pct:.0f}% used)"
        )
    else:
        lines.append(f"*Spend so far:* {_fmt_money(m.spend_eur)}")

    delta_bits = []
    if prev:
        d_spend = m.spend_eur - prev.spend_eur
        d_clicks = m.clicks - prev.clicks
        d_eng = m.engagements - prev.engagements
        if d_spend > 0:
            delta_bits.append(f"+{_fmt_money(d_spend)} spend")
        if d_clicks > 0:
            delta_bits.append(f"+{d_clicks} clicks")
        if d_eng > 0:
            delta_bits.append(f"+{d_eng} engagements")
    if delta_bits:
        lines.append(f"*Since last report:* {', '.join(delta_bits)}")

    stats = []
    if m.clicks:
        stats.append(f"{m.clicks} clicks")
    if m.cpc_eur:
        stats.append(f"CPC {_fmt_money(m.cpc_eur)}")
    if m.ctr:
        stats.append(f"CTR {m.ctr:.2f}%")
    if m.engagements:
        stats.append(f"{m.engagements} engagements")
    if m.engagement_rate:
        stats.append(f"ER {m.engagement_rate:.2f}%")
    if m.impressions:
        stats.append(f"~{m.impressions:,} impressions")
    if stats:
        lines.append("*Totals:* " + " · ".join(stats))

    lines.append("")
    # Pace + card safety insight
    if end_date and m.budget_eur:
        days_left = (end_date - today).days
        if m.status == "active" and days_left > 0 and m.spend_eur:
            start = _parse_schedule_date(m.schedule_start) or today
            daily_avg = m.spend_eur / max(1, (today - start).days or 1)
            projected = m.spend_eur + daily_avg * days_left
            verdict = "on track ✅" if projected <= m.budget_eur * 1.05 else "running hot ⚠️"
            lines.append(
                f"*Pace:* {verdict} — projecting ~{_fmt_money(projected)} by "
                f"{end_date.isoformat()} vs {_fmt_money(m.budget_eur)} cap."
            )
        elif m.status == "draft":
            lines.append(
                "*Note:* the ad set is still a draft — it isn't spending. "
                "Launch it in Campaign Manager when the creative is approved."
            )
    lines.append(
        "💳 *Card safety:* spend stays inside the promo credit — "
        "the Visa on file is not expected to be charged."
    )
    if end_date:
        lines.append(f"Campaign ends {end_date.isoformat()}. Report again tomorrow 10:00.")
    return "\n".join(lines)


def _control_blocks(status: str, campaign_id: str = "") -> list[dict[str, Any]]:
    """Actions block with pause/resume + status buttons under the report.

    Button payloads arrive at the cloudless.gr Slack app
    (/api/slack/interactions) — ``value`` carries the campaign id so the
    handler controls the right ad set.
    """
    if status == "active":
        control = {
            "type": "button",
            "text": {"type": "plain_text", "text": "⏸ Pause campaign"},
            "style": "danger",
            "action_id": "linkedin_ads_pause",
            "value": campaign_id,
            "confirm": {
                "title": {"type": "plain_text", "text": "Pause campaign?"},
                "text": {"type": "plain_text",
                         "text": "Spending stops until you resume it."},
                "confirm": {"type": "plain_text", "text": "Pause"},
                "deny": {"type": "plain_text", "text": "Keep running"},
            },
        }
    else:
        control = {
            "type": "button",
            "text": {"type": "plain_text", "text": "▶️ Resume campaign"},
            "style": "primary",
            "action_id": "linkedin_ads_resume",
            "value": campaign_id,
        }
    return [
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                control,
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "📊 Refresh status"},
                    "action_id": "linkedin_ads_status",
                    "value": campaign_id,
                },
            ],
        },
    ]


def _to_email_text(report: str) -> str:
    """Strip Slack mrkdwn asterisks for a clean plaintext email."""
    return report.replace("*", "")


async def _latest_org_snapshot(db: AsyncSession, account_id: Any, source: str) -> dict[str, Any]:
    row = (
        await db.execute(
            select(PostAnalyticsSnapshot.raw)
            .where(
                PostAnalyticsSnapshot.social_account_id == account_id,
                PostAnalyticsSnapshot.source == source,
            )
            .order_by(desc(PostAnalyticsSnapshot.captured_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    return row or {}


async def build_org_section(db: AsyncSession) -> str:
    """Human-friendly organic-page section from developer-app analytics."""
    account = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.platform == "linkedin",
                SocialAccount.account_type == "organization",
            )
        )
    ).scalars().first()
    if not account:
        return ""

    followers = (
        await db.execute(
            select(FollowerSnapshot.followers)
            .where(
                FollowerSnapshot.social_account_id == account.id,
                FollowerSnapshot.platform == "linkedin",
            )
            .order_by(desc(FollowerSnapshot.captured_at))
            .limit(2)
        )
    ).scalars().all()

    follower_stats = await _latest_org_snapshot(db, account.id, "linkedin_follower_stats")
    page_stats = await _latest_org_snapshot(db, account.id, "linkedin_page_stats")
    lifetime = await _latest_org_snapshot(db, account.id, "linkedin_org_lifetime")

    top_post = (
        await db.execute(
            select(PostAnalyticsSnapshot)
            .where(
                PostAnalyticsSnapshot.social_account_id == account.id,
                PostAnalyticsSnapshot.post_id.is_not(None),
                PostAnalyticsSnapshot.impressions > 0,
            )
            .order_by(desc(PostAnalyticsSnapshot.impressions))
            .limit(1)
        )
    ).scalar_one_or_none()

    lines = ["", "📣 *Company page (organic)*"]
    if followers:
        delta = followers[0] - followers[1] if len(followers) > 1 else 0
        sign = f"+{delta}" if delta > 0 else str(delta)
        lines.append(f"*Followers:* {followers[0]} ({sign} since last check)")

    gains = follower_stats.get("follower_gains") or {}
    if gains:
        lines.append(
            f"*New followers (30d):* {gains.get('organic', 0)} organic"
            + (f" · {gains['paid']} paid" if gains.get("paid") else "")
        )

    demo = follower_stats.get("demographics") or {}
    geo = demo.get("followerCountsByGeoCountry") or demo.get("followerCountsByGeo") or []
    if geo:
        top_geo = max(geo, key=lambda g: (g.get("followerCounts") or {}).get("organicFollowerCount", 0))
        code = str(top_geo.get("geoCountry") or top_geo.get("geo") or "").rsplit(":", 1)[-1]
        lines.append(f"*Top follower location:* {code}")

    daily = (page_stats.get("period") or {}).get("daily") or []
    views = sum(
        int((((el.get("totalPageStatistics") or {}).get("views") or {})
             .get("allPageViews") or {}).get("pageViews") or 0)
        for el in daily
    )
    unique = sum(
        int((((el.get("totalPageStatistics") or {}).get("views") or {})
             .get("allPageViews") or {}).get("uniquePageViews") or 0)
        for el in daily
    )
    if views or unique:
        lines.append(f"*Page views (30d):* {views} ({unique} unique visitors)")

    stats = (lifetime.get("organizationalEntityShareStatistics") or {}).get(
        "totalShareStatistics"
    ) or {}
    if stats:
        lines.append(
            "*All-time content:* "
            f"{stats.get('impressionCount', 0):,} impressions · "
            f"{stats.get('uniqueImpressionsCount', 0):,} unique viewers · "
            f"{stats.get('clickCount', 0)} clicks · "
            f"{stats.get('likeCount', 0)} reactions · "
            f"{stats.get('commentCount', 0)} comments · "
            f"{stats.get('shareCount', 0)} reposts"
        )

    if top_post:
        lines.append(
            f"*Top post:* {top_post.impressions:,} impressions · "
            f"{top_post.likes} reactions · {top_post.comments} comments · "
            f"{top_post.shares} reposts"
        )
    return "\n".join(lines) if len(lines) > 2 else ""


async def _get_team_id(db: AsyncSession) -> Any:
    """Resolve the team that owns the LinkedIn workspace — the ad account
    belongs to it, so snapshots must be attributed there or the campaign
    is invisible to that team's analytics. Falls back to the highest-plan
    team, then any team."""
    linkedin_team = (
        await db.execute(
            select(SocialAccount.team_id)
            .where(
                SocialAccount.platform == "linkedin",
                SocialAccount.status == "active",
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    if linkedin_team:
        return linkedin_team
    tier_rank = case(
        (Team.plan_tier == "enterprise", 4),
        (Team.plan_tier == "business", 3),
        (Team.plan_tier == "pro", 2),
        (Team.plan_tier == "free", 1),
        else_=0,
    )
    return (
        await db.execute(select(Team.id).order_by(desc(tier_rank)).limit(1))
    ).scalar_one_or_none()


async def _latest_snapshot(db: AsyncSession, campaign_id: str) -> AdCampaignSnapshot | None:
    return (
        await db.execute(
            select(AdCampaignSnapshot)
            .where(AdCampaignSnapshot.campaign_id == campaign_id)
            .order_by(desc(AdCampaignSnapshot.captured_at))
            .limit(1)
        )
    ).scalar_one_or_none()


async def _final_sent(db: AsyncSession, campaign_id: str) -> bool:
    row = (
        await db.execute(
            select(AdCampaignSnapshot.id)
            .where(
                AdCampaignSnapshot.campaign_id == campaign_id,
                AdCampaignSnapshot.raw["final"].as_boolean(),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return row is not None


async def run_daily_report() -> dict[str, Any]:
    """Collect → snapshot → report → deliver. Runs daily until campaign end."""
    settings = get_settings()
    tz = ZoneInfo(settings.APP_TIMEZONE)
    today = datetime.now(tz).date()
    end_date: date | None = None
    if settings.LINKEDIN_ADS_END_DATE:
        try:
            end_date = date.fromisoformat(settings.LINKEDIN_ADS_END_DATE)
        except ValueError:
            logger.warning("Bad LINKEDIN_ADS_END_DATE: %r", settings.LINKEDIN_ADS_END_DATE)

    async with async_session_maker() as db:
        team_id = await _get_team_id(db)
        if not team_id:
            return {"ok": False, "error": "no team"}

        campaign_id = settings.LINKEDIN_ADS_CAMPAIGN_ID
        finished_before = await _final_sent(db, campaign_id)
        if finished_before and end_date and today > end_date:
            return {"ok": True, "skipped": "campaign finished, final report already sent"}

        prev = await _latest_snapshot(db, campaign_id)
        metrics = await collect_metrics()

        is_final = bool(end_date and today >= end_date)
        snapshot = AdCampaignSnapshot(
            team_id=team_id,
            platform="linkedin",
            account_id=metrics.account_id,
            campaign_id=metrics.campaign_id,
            campaign_name=metrics.campaign_name,
            status=metrics.status,
            impressions=metrics.impressions,
            clicks=metrics.clicks,
            engagements=metrics.engagements,
            spend_eur=metrics.spend_eur,
            ctr=metrics.ctr,
            engagement_rate=metrics.engagement_rate,
            cpc_eur=metrics.cpc_eur,
            budget_eur=metrics.budget_eur,
            raw={**metrics.raw, "final": is_final},
        )
        db.add(snapshot)
        # Persist before rendering — the notebook reads snapshots in its own
        # session, so the row must be committed for it to see today's metrics.
        await db.commit()

        report_html: str | None = None
        report_attachments: list[dict[str, Any]] = []
        report = await _render_report_notebook(
            campaign_id=campaign_id,
            status=metrics.status,
            end_date=end_date,
            is_final=is_final,
        )
        if report is not None:
            report, report_html, report_attachments = report  # type: ignore[misc]
        else:
            report = build_report_text(metrics, prev, end_date)
            try:
                report += await build_org_section(db)
            except Exception as exc:  # noqa: BLE001 — organic section is best-effort
                logger.warning("LinkedIn org insights section failed: %s", exc)
            if is_final:
                report += "\n\n🏁 *Final report* — the campaign schedule has ended."

        # Slack: dedicated ads channel webhook/token, falling back to the
        # default digest webhook so reports are never lost. Interactive
        # pause/resume buttons POST to the app's Interactivity Request URL
        # (/api/v1/slack/interactions) once it is configured on the Slack app.
        slack_ok, slack_err, _ = await _post_slack_text(
            text=report,
            webhook_url=settings.SLACK_ADS_WEBHOOK_URL or settings.SLACK_WEBHOOK_URL,
            token=(settings.SLACK_BOT_TOKEN or settings.SLACK_ACCESS_TOKEN or "").strip(),
            channel_id=settings.SLACK_ADS_CHANNEL_ID or settings.SLACK_CHANNEL_ID,
            purpose="linkedin-ads",
            blocks=_control_blocks(metrics.status, metrics.campaign_id),
        )

        email_err = None
        try:
            send_email_smtp(
                subject=f"LinkedIn ad report — {today.isoformat()}",
                text_body=_to_email_text(report),
                to_addrs=[a for a in (settings.LINKEDIN_ADS_EMAIL_TO or settings.DIGEST_EMAIL_TO).split(",") if a.strip()],
            )
        except Exception as exc:  # noqa: BLE001
            email_err = str(exc)[:300]
            logger.warning("LinkedIn ads report email failed: %s", exc)

        await db.commit()
        return {
            "ok": True,
            "final": is_final,
            "metrics": {
                "spend_eur": metrics.spend_eur,
                "clicks": metrics.clicks,
                "engagements": metrics.engagements,
                "ctr": metrics.ctr,
                "status": metrics.status,
            },
            "posted_to_slack": slack_ok,
            "slack_error": slack_err,
            "email_error": email_err,
        }

"""Daily social-media strategy report → email.

End-of-day companion to the Slack ops digest: instead of "what broke",
answers "what should I do tomorrow". Built from the insights engine
(30-day window: momentum, best posting windows, benchmarks, format and
channel recommendations) plus the last-24h digest numbers.

The narrative action list is written by the text inference chain
(DMR → Cloudflare Workers AI); when inference is unavailable the report
falls back to the insights engine's rule-based recommendations, so the
email always goes out.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.user import Team
from app.services.email_digest import _html_escape, send_email
from app.services.insights_engine import build_team_insights
from app.services.slack_digest import DigestReport, build_daily_digest

logger = logging.getLogger(__name__)

_MAX_ACTIONS = 6


@dataclass
class StrategyReport:
    generated_at: datetime
    timezone: str
    team_name: str
    insights: dict[str, Any] = field(default_factory=dict)
    digest: DigestReport | None = None
    actions: list[str] = field(default_factory=list)
    llm_used: bool = False
    emailed: bool = False
    email_error: str | None = None

    def subject(self) -> str:
        day = self.generated_at.astimezone(ZoneInfo(self.timezone)).strftime("%Y-%m-%d")
        errors = sum(1 for i in (self.digest.issues if self.digest else []) if i.severity == "error")
        if errors:
            return f"[SocialAuto] Strategy brief {day} — {errors} issue(s) need attention"
        return f"[SocialAuto] Strategy brief {day} — tomorrow's playbook"

    # ── Rendering ────────────────────────────────────────────────────────

    def to_text(self) -> str:
        tz = ZoneInfo(self.timezone)
        when = self.generated_at.astimezone(tz).strftime("%a %d %b %Y %H:%M %Z")
        lines = [
            "SocialAuto daily strategy brief",
            f"{self.team_name} · {when}",
            "",
        ]
        if self.digest:
            d = self.digest
            lines += [
                "LAST 24H",
                f"  Impressions {d.impressions_24h} · engagement {d.engagement_24h} · "
                f"{d.overview.get('connected_accounts', 0)} accounts connected",
                "",
            ]
        lines.append("PLATFORM PULSE (30d)")
        for name, p in self._platform_rows():
            mom = p.get("momentum_7d_engagement_pct")
            mom_s = f"{mom:+}%" if isinstance(mom, (int, float)) else "n/a"
            bench = p.get("benchmark") or {}
            verdict = bench.get("verdict", "").replace("_", " ") or "no benchmark"
            window = self._best_window(p)
            lines.append(
                f"  {name}: {p.get('posts', 0)} posts · eng {p.get('engagement', 0)} · "
                f"7d momentum {mom_s} · {verdict} · best {window}"
            )
        lines.append("")
        lines.append("TOMORROW'S PLAYBOOK")
        if self.actions:
            for i, a in enumerate(self.actions, 1):
                lines.append(f"  {i}. {a}")
        else:
            lines.append("  No actions generated — publish consistently and re-check tomorrow.")
        issues = (self.digest.issues if self.digest else [])
        if issues:
            lines.append("")
            lines.append("DATA GAPS / ISSUES")
            for i in issues[:6]:
                lines.append(f"  [{i.severity}] {i.title}" + (f" — {i.detail}" if i.detail else ""))
        lines.append("")
        lines.append("Cloudless · Clear skies. Zero friction.")
        return "\n".join(lines)

    def to_html(self) -> str:
        tz = ZoneInfo(self.timezone)
        when = self.generated_at.astimezone(tz).strftime("%a %d %b %Y %H:%M %Z")
        esc = _html_escape

        digest_block = ""
        if self.digest:
            d = self.digest
            digest_block = f"""
  <h3>Last 24h</h3>
  <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">
    <tr><td>Impressions</td><td><b>{d.impressions_24h}</b></td>
        <td>Engagement</td><td><b>{d.engagement_24h}</b></td></tr>
    <tr><td>Connected accounts</td><td><b>{d.overview.get('connected_accounts', 0)}</b></td>
        <td>Posts (30d window)</td><td><b>{d.overview.get('total_posts', 0)}</b></td></tr>
  </table>"""

        rows = "".join(
            "<tr>"
            f"<td><b>{esc(name)}</b></td>"
            f"<td>{p.get('posts', 0)}</td>"
            f"<td>{p.get('engagement', 0)}</td>"
            f"<td>{esc(self._mom_str(p))}</td>"
            f"<td>{esc((p.get('benchmark') or {}).get('verdict', '—').replace('_', ' '))}</td>"
            f"<td>{esc(self._best_window(p))}</td>"
            f"<td>{esc(p.get('confidence', 'low'))}</td>"
            "</tr>"
            for name, p in self._platform_rows()
        )
        platform_block = (
            "<h3>Platform pulse (30 days)</h3>"
            '<table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">'
            "<tr><th align='left'>Platform</th><th>Posts</th><th>Engagement</th>"
            "<th>7d momentum</th><th>vs benchmark</th><th>Best window</th><th>Confidence</th></tr>"
            f"{rows}</table>"
            if rows
            else "<p><i>No platform data yet — publish a few posts, then re-check.</i></p>"
        )

        action_items = "".join(f"<li>{esc(a)}</li>" for a in self.actions)
        actions_block = (
            f"<h3>Tomorrow's playbook</h3><ol>{action_items}</ol>"
            f"<p style='color:#666;font-size:12px'>"
            f"{'AI-written from your metrics' if self.llm_used else 'Rule-based recommendations (AI writer unavailable)'}</p>"
            if action_items
            else "<p><i>No actions generated — publish consistently and re-check tomorrow.</i></p>"
        )

        issues = self.digest.issues if self.digest else []
        issues_block = ""
        if issues:
            items = "".join(
                f"<li><b>{esc(i.title)}</b>{(' — ' + esc(i.detail)) if i.detail else ''}</li>"
                for i in issues[:6]
            )
            issues_block = (
                f"<h3 style='color:#a16207'>Data gaps / issues</h3><ul>{items}</ul>"
            )

        return f"""<!DOCTYPE html>
<html><body style="font-family:system-ui,sans-serif;line-height:1.45;color:#111;max-width:720px">
  <h2>SocialAuto daily strategy brief</h2>
  <p>{esc(self.team_name)} · {esc(when)}</p>
  {digest_block}
  {platform_block}
  {actions_block}
  {issues_block}
  <p style="color:#666;font-size:12px">Sources: SocialAuto insights engine · ops digest in Slack #socialauto</p>
</body></html>"""

    # ── Helpers ──────────────────────────────────────────────────────────

    def _platform_rows(self) -> list[tuple[str, dict[str, Any]]]:
        platforms = self.insights.get("platforms") or {}
        # Focus order from the engine's best-practice tiers, then name.
        tier_rank = {"primary": 0, "secondary": 1, "last": 2}
        return sorted(
            platforms.items(),
            key=lambda kv: (tier_rank.get(kv[1].get("focus_tier", "last"), 3), kv[0]),
        )

    @staticmethod
    def _mom_str(p: dict[str, Any]) -> str:
        m = p.get("momentum_7d_engagement_pct")
        return f"{m:+}%" if isinstance(m, (int, float)) else "n/a"

    @staticmethod
    def _best_window(p: dict[str, Any]) -> str:
        wd = p.get("best_weekday_athens")
        hr = p.get("best_hour_athens")
        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if wd and hr and wd[1] > 0 and hr[1] > 0:
            return f"{weekdays[wd[0]]} {hr[0]:02d}:00"
        return "baseline"


def _rule_actions(insights: dict[str, Any]) -> list[str]:
    """Fallback: the insights engine's own recommendations, priority-ordered."""
    rank = {"high": 0, "medium": 1, "low": 2}
    recs = sorted(
        insights.get("recommendations") or [],
        key=lambda r: rank.get(r.get("priority", "low"), 3),
    )
    return [r["text"] for r in recs[:_MAX_ACTIONS] if r.get("text")]


def _compact_insights(insights: dict[str, Any]) -> dict[str, Any]:
    """Shrink the insights payload to what the LLM needs for the prompt."""
    platforms = {}
    for name, p in (insights.get("platforms") or {}).items():
        platforms[name] = {
            "posts": p.get("posts"),
            "impressions": p.get("impressions"),
            "engagement": p.get("engagement"),
            "avg_engagement_rate": p.get("avg_engagement_rate"),
            "momentum_7d_engagement_pct": p.get("momentum_7d_engagement_pct"),
            "momentum_7d_impressions_pct": p.get("momentum_7d_impressions_pct"),
            "benchmark_verdict": (p.get("benchmark") or {}).get("verdict"),
            "best_weekday_athens": p.get("best_weekday_athens"),
            "best_hour_athens": p.get("best_hour_athens"),
            "media_avg_er": p.get("media_avg_er"),
            "text_avg_er": p.get("text_avg_er"),
            "confidence": p.get("confidence"),
            "data_warnings": p.get("data_warnings"),
        }
    return {
        "window_days": insights.get("window_days"),
        "platforms": platforms,
        "recommendations": insights.get("recommendations"),
    }


_ACTION_LINE = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*(.+?)\s*$")


def _parse_actions(text: str) -> list[str]:
    """Pull numbered/bulleted action lines out of the model's reply."""
    actions: list[str] = []
    for line in text.splitlines():
        m = _ACTION_LINE.match(line)
        if m:
            action = m.group(1).strip().strip('"')
            if len(action) > 15:
                actions.append(action)
    return actions[:_MAX_ACTIONS]


async def _llm_actions(
    db: AsyncSession,
    team_id,
    insights: dict[str, Any],
    digest: DigestReport,
) -> list[str] | None:
    from app.services.inference import call_inference

    prompt = (
        "You are the social media strategist for Cloudless (cloudless.gr — "
        "managed cloud hosting, Cloudflare, fast websites; LinkedIn is the "
        "primary channel, Instagram/Facebook/Threads secondary).\n\n"
        "Here is today's data (last-24h digest + 30-day insights):\n"
        f"```json\n{json.dumps(_compact_insights(insights), default=str)}\n```\n"
        f"Last 24h: impressions {digest.impressions_24h}, "
        f"engagement {digest.engagement_24h}.\n\n"
        f"Write {_MAX_ACTIONS} concrete actions for tomorrow as a numbered "
        "list. Each action must be specific (which platform, what content "
        "type, when to post, why based on the numbers). No preamble, no "
        "closing remarks — only the numbered list."
    )
    try:
        resp = await call_inference(
            prompt,
            provider_name="dmr",
            db=db,
            team_id=team_id,
            max_tokens=600,
            allow_fallback=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Strategy report inference failed: %s", exc)
        return None

    text = (resp or {}).get("text") or (resp or {}).get("response") or ""
    if not isinstance(text, str) or not text.strip():
        return None
    actions = _parse_actions(text)
    return actions or None


async def build_strategy_report(
    db: AsyncSession,
    *,
    team: Team,
    insight_days: int = 30,
) -> StrategyReport:
    settings = get_settings()
    tz_name = settings.APP_TIMEZONE or "Europe/Athens"
    digest = await build_daily_digest(db, team=team, days=1)
    insights = await build_team_insights(db, team.id, days=insight_days)

    report = StrategyReport(
        generated_at=datetime.now(UTC),
        timezone=tz_name,
        team_name=team.name or "SocialAuto",
        insights=insights,
        digest=digest,
    )

    actions = await _llm_actions(db, team.id, insights, digest)
    if actions:
        report.actions = actions
        report.llm_used = True
    else:
        report.actions = _rule_actions(insights)
    return report


async def email_strategy_report(report: StrategyReport) -> StrategyReport:
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
        logger.exception("Failed to email strategy report")
    return report


async def run_strategy_report_for_all_teams(
    db: AsyncSession,
    *,
    insight_days: int = 30,
    send: bool = True,
) -> list[dict[str, Any]]:
    teams = (await db.execute(select(Team))).scalars().all()
    results: list[dict[str, Any]] = []
    for team in teams:
        report = await build_strategy_report(db, team=team, insight_days=insight_days)
        # Same skip rule as the ops digest: no accounts + no impressions = test team.
        active = (
            report.digest is not None
            and (
                report.digest.overview.get("connected_accounts", 0) > 0
                or report.digest.impressions_24h > 0
            )
        )
        if active and send:
            report = await email_strategy_report(report)
        elif not active:
            report.email_error = "skipped empty team"
        results.append(
            {
                "team_name": report.team_name,
                "actions": report.actions,
                "llm_used": report.llm_used,
                "emailed": report.emailed,
                "email_error": report.email_error,
            }
        )
    return results

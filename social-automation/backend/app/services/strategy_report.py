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
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import get_settings
from app.models.content import MediaAsset, Post, PostStatus, PostTarget
from app.models.user import Team
from app.services.email_digest import _html_escape, send_email
from app.services.insights_engine import build_team_insights
from app.services.publishing import _media_public_url
from app.services.slack_digest import DigestReport, build_daily_digest
from app.services.tiktok_api import is_tiktok_publish_id

logger = logging.getLogger(__name__)

_MAX_ACTIONS = 6
_MAX_POSTS_PER_PLATFORM = 4
_CONTENT_PREVIEW_CHARS = 90
_RECENT_POSTS_DAYS = 7


@dataclass
class BriefMedia:
    """One media asset attached to a recent published post."""

    filename: str | None = None
    mime_type: str | None = None
    url: str | None = None
    is_image: bool = False


@dataclass
class BriefPost:
    """A published post summary for the strategy brief media section."""

    post_id: str
    content_preview: str = ""
    published_at: datetime | None = None
    platform_url: str | None = None
    media: list[BriefMedia] = field(default_factory=list)
    missing_media: bool = False
    pending_inbox: bool = False  # TikTok MEDIA_UPLOAD draft awaiting app publish

    @property
    def id_prefix(self) -> str:
        return (self.post_id or "")[:8]


@dataclass
class PlaybookItem:
    """One scheduled action for tomorrow — parsed from the action text so it
    can be rendered both as friendly prose and as a machine-readable
    deployment block for an automation agent."""

    instruction: str
    platform: str = ""
    time_athens: str = ""        # "HH:MM" Athens local
    format: str = ""             # text | image | carousel | video
    media_required: bool = False


_KNOWN_PLATFORMS = (
    "linkedin", "instagram", "facebook", "threads", "twitter", "x",
    "tiktok", "telegram", "whatsapp", "youtube",
)
_TIME_RE = re.compile(r"\b([01]?\d|2[0-3]):([0-5]\d)\b")


def _parse_playbook_item(action: str) -> PlaybookItem:
    """Extract platform / Athens time / format hints from an action line.

    Anything not recognised is left blank — the full instruction text is
    always carried through, so no detail is lost for the agent.
    """
    low = action.lower()
    # The destination platform usually follows a preposition ("post on
    # Threads", "share to LinkedIn"); a platform mentioned earlier may be the
    # source ("repurpose a top LinkedIn post …"). Prefer preposition-led
    # mentions, else the last mention.
    prep_hits = [
        m.group(1)
        for m in re.finditer(
            r"\b(?:on|to|for|via|over)\s+(?:the\s+)?("
            + "|".join(_KNOWN_PLATFORMS)
            + r")\b",
            low,
        )
    ]
    if prep_hits:
        platform = prep_hits[0]
    else:
        mentions = [
            m.group(0)
            for m in re.finditer(
                r"\b(" + "|".join(_KNOWN_PLATFORMS) + r")\b", low
            )
        ]
        platform = mentions[-1] if mentions else ""
    if platform == "x":
        platform = "twitter"
    m = _TIME_RE.search(action)
    time_athens = f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""
    fmt = ""
    if "carousel" in low:
        fmt = "carousel"
    elif "video" in low or "reel" in low:
        fmt = "video"
    elif re.search(r"\b(image|photo|media-rich|media)\b", low):
        fmt = "image"
    elif re.search(r"\btext\b", low):
        fmt = "text"
    return PlaybookItem(
        instruction=action,
        platform=platform,
        time_athens=time_athens,
        format=fmt,
        media_required=fmt in {"image", "carousel", "video"},
    )


def _agent_playbook_block(items: list[PlaybookItem], tz_name: str) -> str:
    """Machine-readable playbook for a Devin/automation agent — JSON fenced
    block that can be pasted verbatim. Times are Athens local; the agent is
    expected to convert to UTC when scheduling via the SocialAuto API."""
    payload = {
        "timezone": tz_name,
        "source": "socialauto-daily-strategy-brief",
        "actions": [
            {
                "platform": it.platform or None,
                "time_local": it.time_athens or None,
                "format": it.format or None,
                "media_required": it.media_required,
                "instruction": it.instruction,
            }
            for it in items
        ],
    }
    return "```json\n" + json.dumps(payload, indent=2, ensure_ascii=False) + "\n```"


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
    recent_posts_by_platform: dict[str, list[BriefPost]] = field(default_factory=dict)
    initiatives: list[dict[str, Any]] = field(default_factory=list)

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
            mom_s = self._mom_str(p)
            verdict = self._bench_cell(p)
            window = self._best_window(p)
            er_s = self._er_str(p)
            imp = p.get("impressions")
            imp_s = str(imp) if isinstance(imp, int | float) else "n/a"
            flag = " [sync⚠]" if p.get("data_warnings") else ""
            lines.append(
                f"  {name}{flag}: {p.get('posts', 0)} posts · {imp_s} impressions · "
                f"eng {p.get('engagement', 0)} · avg ER {er_s} · "
                f"7d momentum {mom_s} · {verdict} · best {window} · "
                f"confidence {p.get('confidence', 'low')}"
            )
        warned = [
            f"{n}: {p['data_warnings'][0]}"
            for n, p in self._platform_rows()
            if p.get("data_warnings")
        ]
        for w in warned:
            lines.append(f"  ⚠ {w}")
        takeaway = self._pulse_takeaway()
        if takeaway:
            lines.append(f"  → {takeaway}")
        lines.append("")
        lines.extend(self._recent_posts_text(tz))
        lines.append("TOMORROW'S PLAYBOOK")
        if self.actions:
            for i, a in enumerate(self.actions, 1):
                lines.append(f"  {i}. {a}")
            items = sorted(
                (_parse_playbook_item(a) for a in self.actions),
                key=lambda it: it.time_athens or "99:99",
            )
            lines.append("")
            lines.append("AGENT DEPLOYMENT BLOCK — paste to your Devin agent")
            lines.append(_agent_playbook_block(items, self.timezone))
        else:
            lines.append("  No actions generated — publish consistently and re-check tomorrow.")
        lines.extend(self._initiatives_text())
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
        <td>Posts created (24h)</td><td><b>{d.overview.get('total_posts', 0)}</b></td></tr>
  </table>"""

        rows = "".join(
            "<tr>"
            f"<td><b>{esc(name)}</b>"
            + (" <span title='collection issues'>⚠</span>" if p.get("data_warnings") else "")
            + "</td>"
            f"<td>{p.get('posts', 0)}</td>"
            f"<td>{p.get('impressions', 'n/a')}</td>"
            f"<td>{p.get('engagement', 0)}</td>"
            f"<td>{esc(self._er_str(p))}</td>"
            f"<td>{esc(self._mom_str(p))}</td>"
            f"<td>{esc(self._bench_cell(p))}</td>"
            f"<td>{esc(self._best_window(p))}</td>"
            f"<td>{esc(p.get('confidence', 'low'))}</td>"
            "</tr>"
            for name, p in self._platform_rows()
        )
        warn_notes = "; ".join(
            f"{name}: {p['data_warnings'][0]}"
            + (f" (+{len(p['data_warnings']) - 1} more)" if len(p["data_warnings"]) > 1 else "")
            for name, p in self._platform_rows()
            if p.get("data_warnings")
        )
        takeaway = self._pulse_takeaway()
        platform_block = (
            "<h3>Platform pulse (30 days)</h3>"
            '<table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">'
            "<tr><th align='left'>Platform</th><th>Posts</th><th>Impressions</th>"
            "<th>Engagement</th><th>Avg ER</th><th>7d momentum</th>"
            "<th>vs benchmark</th><th>Best window</th><th>Confidence</th></tr>"
            f"{rows}</table>"
            + (f"<p style='color:#374151;font-size:13px'>→ {esc(takeaway)}</p>" if takeaway else "")
            + (
                f"<p style='color:#a16207;font-size:12px'>⚠ sync gaps — {esc(warn_notes)}</p>"
                if warn_notes else ""
            )
            if rows
            else "<p><i>No platform data yet — publish a few posts, then re-check.</i></p>"
        )

        recent_block = self._recent_posts_html(tz, esc)

        action_rows = ""
        agent_block_html = ""
        if self.actions:
            items = sorted(
                (_parse_playbook_item(a) for a in self.actions),
                key=lambda it: it.time_athens or "99:99",
            )
            action_rows = "".join(
                "<tr>"
                f"<td><b>{esc(it.time_athens or '—')}</b></td>"
                f"<td>{esc(it.platform.capitalize() if it.platform else '—')}</td>"
                f"<td>{esc(it.format.capitalize() if it.format else '—')}"
                f"{' 📎' if it.media_required else ''}</td>"
                f"<td>{esc(it.instruction)}</td>"
                "</tr>"
                for it in items
            )
            agent_block_html = (
                "<details style='margin-top:14px'><summary style='cursor:pointer;"
                "color:#374151;font-size:13px'>🤖 Agent deployment block — "
                "expand and paste to your Devin agent</summary>"
                f"<pre style='background:#f3f4f6;border:1px solid #e5e7eb;"
                f"border-radius:6px;padding:10px;font-size:12px;overflow-x:auto'>"
                f"{esc(_agent_playbook_block(items, self.timezone))}</pre></details>"
            )
        actions_block = (
            "<h3>Tomorrow's playbook</h3>"
            '<table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">'
            "<tr><th>Time (Athens)</th><th>Platform</th><th>Format</th><th align='left'>What &amp; why</th></tr>"
            f"{action_rows}</table>{agent_block_html}"
            f"<p style='color:#666;font-size:12px'>"
            f"{'AI-written from your metrics' if self.llm_used else 'Rule-based recommendations (AI writer unavailable)'}</p>"
            if action_rows
            else "<p><i>No actions generated — publish consistently and re-check tomorrow.</i></p>"
        )

        initiatives_block = self._initiatives_html(esc)

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
  {recent_block}
  {actions_block}
  {initiatives_block}
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

    def _initiatives_text(self) -> list[str]:
        """GROWTH INITIATIVES section — sent / accepted / pending / credits."""
        if not self.initiatives:
            return []
        lines = ["", "GROWTH INITIATIVES"]
        for i in self.initiatives:
            label = i.get("initiative") or i["event_type"]
            if i.get("units"):
                sent = f"{i['units']} sent"
                if i.get("units_this_month") is not None and (
                    i["units_this_month"] != i["units"]
                ):
                    sent += f" ({i['units_this_month']} this month)"
            else:
                sent = f"{i['events']} event{'s' if i['events'] != 1 else ''}"
            lines.append(f"  {label} ({i['platform']}): {sent}")
            funnel = []
            if i.get("accepted_est") is not None:
                funnel.append(f"~{i['accepted_est']} accepted (new followers)")
            if i.get("pending_est"):
                funnel.append(f"~{i['pending_est']} still waiting")
            if i.get("declined"):
                funnel.append(f"{i['declined']} declined")
            if i.get("conversion_pct") is not None:
                funnel.append(f"{i['conversion_pct']}% acceptance rate")
            if funnel:
                lines.append(f"    {' · '.join(funnel)}")
            if i.get("followers_start") is not None:
                lines.append(
                    f"    Page followers: {i['followers_start']} → "
                    f"{i['followers_now']} ({i['followers_delta']:+d})"
                )
            if i.get("credits_left") is not None:
                lines.append(
                    f"    Invitation credits: ~{i['credits_left']} of "
                    f"{i['monthly_cap']} left this month — every accepted "
                    "invite refunds one credit (≤72h), so good targeting "
                    "stretches the pool."
                )
        return lines

    def _initiatives_html(self, esc) -> str:
        if not self.initiatives:
            return ""
        cards = ""
        for i in self.initiatives:
            delta = i.get("followers_delta")
            conv = i.get("conversion_pct")
            funnel = []
            if i.get("accepted_est") is not None:
                funnel.append(f"<b>{i['accepted_est']}</b> accepted")
            if i.get("pending_est"):
                funnel.append(f"<b>{i['pending_est']}</b> waiting")
            if i.get("declined"):
                funnel.append(f"<b>{i['declined']}</b> declined")
            stats = " · ".join(funnel)
            if conv is not None:
                stats += f" — <b>{conv}%</b> acceptance"
            followers = ""
            if i.get("followers_start") is not None:
                followers = (
                    "<br>Page followers: "
                    f"{i['followers_start']} → <b>{i['followers_now']}</b> "
                    f"({f'{delta:+d}' if delta is not None else '—'})"
                )
            credits = ""
            if i.get("credits_left") is not None:
                pct = round(i["credits_left"] / i["monthly_cap"] * 100)
                credits = (
                    f"<br>Credits: <b>~{i['credits_left']}</b> of "
                    f"{i['monthly_cap']} left this month "
                    f'<span style="display:inline-block;width:80px;height:8px;'
                    'background:#e5e7eb;border-radius:4px;vertical-align:middle">'
                    f'<span style="display:block;width:{pct}%;height:8px;'
                    'background:#10b981;border-radius:4px"></span></span>'
                )
            cards += (
                '<div style="margin:8px 0;padding:10px;border:1px solid #e5e7eb;'
                'border-radius:8px">'
                f"<b>{esc(i.get('initiative') or i['event_type'])}</b> "
                f"<span style='color:#6b7280'>({esc(i['platform'])})</span><br>"
                f"{i['units']} invites sent &nbsp;→&nbsp; {stats}"
                f"{followers}{credits}</div>"
            )
        return (
            "<h3>Growth initiatives</h3>"
            '<p style="color:#6b7280;font-size:13px;margin:4px 0">'
            "LinkedIn refunds one invitation credit for every accepted "
            "invite (up to 72h) — high acceptance stretches the monthly "
            "pool. Credits renew on the 1st.</p>"
            f"{cards}"
        )

    def _pulse_takeaway(self) -> str:
        """One plain-English sentence summarising the platform pulse."""
        rows = self._platform_rows()
        if not rows:
            return ""
        top = max(rows, key=lambda kv: kv[1].get("engagement") or 0)
        name, p = top
        eng = p.get("engagement") or 0
        if eng <= 0:
            return ""
        mom = p.get("momentum_7d_engagement_pct")
        mom_s = (
            f" and momentum is {mom:+.0f}% week-over-week"
            if isinstance(mom, int | float) and abs(mom) >= 50
            else ""
        )
        return (
            f"{name.capitalize()} is your strongest channel this month "
            f"({eng} engagements{mom_s}) — weight tomorrow's effort there."
        )

    def _recent_platform_sections(self) -> list[tuple[str, list[BriefPost]]]:
        """Platforms with recent posts — pulse order first, then alpha."""
        if not self.recent_posts_by_platform:
            return []
        pulse_names = [n for n, _ in self._platform_rows()]
        seen: set[str] = set()
        out: list[tuple[str, list[BriefPost]]] = []
        for name in pulse_names:
            posts = self.recent_posts_by_platform.get(name)
            if posts:
                out.append((name, posts))
                seen.add(name)
        for name in sorted(self.recent_posts_by_platform):
            if name not in seen and self.recent_posts_by_platform[name]:
                out.append((name, self.recent_posts_by_platform[name]))
        return out

    def _fmt_published(self, when: datetime | None, tz: ZoneInfo) -> str:
        if when is None:
            return "unknown time"
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return when.astimezone(tz).strftime("%a %d %b %Y %H:%M %Z")

    def _recent_posts_text(self, tz: ZoneInfo) -> list[str]:
        sections = self._recent_platform_sections()
        lines = ["RECENT POSTS & MEDIA (7 days)"]
        if not sections:
            lines.append("  No published posts in the last 7 days.")
            lines.append("")
            return lines
        for platform, posts in sections:
            lines.append(f"  {platform}:")
            for bp in posts:
                preview = bp.content_preview or "(no text)"
                bit = f'    - "{preview}" · {bp.id_prefix} · {self._fmt_published(bp.published_at, tz)}'
                if bp.platform_url:
                    bit += f" · {bp.platform_url}"
                lines.append(bit)
                if bp.pending_inbox:
                    lines.append(
                        "      ⌛ Draft in TikTok app inbox — finish in the app to go live"
                    )
                elif bp.missing_media:
                    lines.append("      ⚠ Missing media — every post must have correct media")
                elif bp.media:
                    parts: list[str] = []
                    for m in bp.media:
                        label = m.filename or ("image" if m.is_image else "media")
                        if m.is_image and m.url:
                            parts.append(f"[img] {m.url}")
                        elif m.url:
                            parts.append(f"▶ {label} ({m.url})")
                        else:
                            parts.append(f"▶ {label}")
                    lines.append("      media: " + "; ".join(parts))
        lines.append("")
        return lines

    def _recent_posts_html(self, tz: ZoneInfo, esc) -> str:
        sections = self._recent_platform_sections()
        if not sections:
            return (
                "<h3>Recent posts &amp; media (7 days)</h3>"
                "<p><i>No published posts in the last 7 days.</i></p>"
            )

        blocks: list[str] = [
            "<h3>Recent posts &amp; media (7 days)</h3>"
        ]
        for platform, posts in sections:
            blocks.append(f"<h4 style='margin:14px 0 6px'>{esc(platform)}</h4>")
            for bp in posts:
                preview = esc(bp.content_preview or "(no text)")
                when_s = esc(self._fmt_published(bp.published_at, tz))
                link = ""
                if bp.platform_url:
                    link = (
                        f' · <a href="{esc(bp.platform_url)}" '
                        f'style="color:#2563eb">view post</a>'
                    )
                media_html = self._media_tiles_html(bp, esc)
                warn = ""
                if bp.pending_inbox:
                    warn = (
                        "<div style='color:#0369a1;font-size:13px;margin-top:6px'>"
                        "⌛ Draft in TikTok app inbox — finish in the app to go live"
                        "</div>"
                    )
                elif bp.missing_media:
                    warn = (
                        "<div style='color:#b45309;font-size:13px;margin-top:6px'>"
                        "⚠ Missing media — every post must have correct media"
                        "</div>"
                    )
                blocks.append(
                    "<div style='margin:0 0 12px;padding:10px 12px;border:1px solid #e5e7eb;"
                    "border-radius:6px;background:#fafafa'>"
                    f"<div style='font-size:14px'><b>{preview}</b></div>"
                    f"<div style='color:#555;font-size:12px;margin-top:4px'>"
                    f"{esc(bp.id_prefix)} · {when_s}{link}</div>"
                    f"{media_html}{warn}</div>"
                )
        return "\n".join(blocks)

    @staticmethod
    def _media_tiles_html(bp: BriefPost, esc) -> str:
        if not bp.media:
            return ""
        tiles: list[str] = []
        for m in bp.media:
            if not m.url:
                continue
            url = esc(m.url)
            name = esc(m.filename or ("image" if m.is_image else "media"))
            if m.is_image:
                tiles.append(
                    f'<a href="{url}" style="display:inline-block;margin:4px 6px 0 0">'
                    f'<img src="{url}" alt="{name}" width="120" height="120" '
                    f'style="object-fit:cover;border-radius:4px;border:1px solid #ddd;'
                    f'display:block"/></a>'
                )
            else:
                tiles.append(
                    f'<a href="{url}" style="display:inline-block;width:120px;height:120px;'
                    f'margin:4px 6px 0 0;border-radius:4px;border:1px solid #ddd;'
                    f'background:#111;color:#fff;text-decoration:none;text-align:center;'
                    f'vertical-align:top">'
                    f'<span style="display:block;padding-top:36px;font-size:28px">▶</span>'
                    f'<span style="display:block;font-size:11px;padding:4px 6px;'
                    f'word-break:break-all">{name}</span></a>'
                )
        if not tiles:
            return ""
        return f"<div style='margin-top:8px'>{''.join(tiles)}</div>"

    @staticmethod
    def _bench_cell(p: dict) -> str:
        """Human-readable ER-by-followers vs baseline for email tables."""
        bench = p.get("benchmark") or {}
        verdict = (bench.get("verdict") or "").replace("_", " ")
        yours = bench.get("your_er_by_followers_pct")
        base = bench.get("benchmark_pct")
        if yours is not None and base is not None:
            return f"ER {yours}% vs {base}% ({verdict})"
        if verdict:
            return verdict
        # The comparison is gated on ≥50 followers — say so instead of
        # showing a dead "—" that reads like missing infrastructure.
        current = (p.get("follower_growth") or {}).get("current")
        if current is not None:
            return f"— (needs 50+ followers, has {current})"
        return "—"

    @staticmethod
    def _er_str(p: dict[str, Any]) -> str:
        er = p.get("avg_engagement_rate")
        if not isinstance(er, int | float):
            return "n/a"
        if (p.get("impressions") or 0) < 50:
            # A 1-engagement/7-impression platform would show "14.3%" —
            # precision the data doesn't support.
            return "low data"
        return f"{er}%"

    @staticmethod
    def _mom_str(p: dict[str, Any]) -> str:
        m = p.get("momentum_7d_engagement_pct")
        if isinstance(m, int | float):
            return f"{m:+}%"
        # No ratio (prior week was zero) — show the raw counts so the cell
        # still carries information instead of an unexplained "n/a".
        recent = p.get("engagement_7d") or 0
        prior = p.get("engagement_prev_7d") or 0
        if recent or prior:
            return f"{prior}→{recent}"
        return "—"

    @staticmethod
    def _best_window(p: dict[str, Any]) -> str:
        wd = p.get("best_weekday_athens")
        hr = p.get("best_hour_athens")
        weekdays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        if wd and hr and wd[1] > 0 and hr[1] > 0:
            return f"{weekdays[wd[0]]} {hr[0]:02d}:00"
        return "baseline"


def _preview_text(text: str | None, limit: int = _CONTENT_PREVIEW_CHARS) -> str:
    """Collapse whitespace and truncate for email previews."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 1, 1)].rstrip() + "…"


def _is_image_asset(mime_type: str | None, filename: str | None) -> bool:
    if mime_type and mime_type.lower().startswith("image/"):
        return True
    if filename:
        lower = filename.lower()
        return lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".avif", ".bmp"))
    return False


def _brief_media_url(asset: MediaAsset) -> str | None:
    """Prefer absolute public_url; else build via publishing._media_public_url."""
    pub = (asset.public_url or "").strip()
    if pub.startswith("http://") or pub.startswith("https://"):
        return pub
    if asset.storage_path:
        return _media_public_url(asset.storage_path)
    return None


def _brief_media_from_assets(
    media_ids: list[UUID] | None,
    assets_by_id: dict[UUID, MediaAsset],
) -> list[BriefMedia]:
    """Resolve MediaAssets in post.media_ids order into BriefMedia with URLs."""
    out: list[BriefMedia] = []
    for mid in media_ids or []:
        asset = assets_by_id.get(mid)
        if not asset:
            continue
        url = _brief_media_url(asset)
        if not url:
            continue
        out.append(
            BriefMedia(
                filename=asset.filename,
                mime_type=asset.mime_type,
                url=url,
                is_image=_is_image_asset(asset.mime_type, asset.filename),
            )
        )
    return out


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
    from app.services.duplicate_detector import is_duplicate

    actions: list[str] = []
    for line in text.splitlines():
        m = _ACTION_LINE.match(line)
        if m:
            action = m.group(1).strip().strip('"')
            if len(action) <= 15:
                continue
            # The model sometimes emits the same action twice with a slightly
            # different tail ("…again"). Drop near-duplicates.
            if any(is_duplicate(action, seen, threshold=0.85) for seen in actions):
                continue
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
        "list, in chronological order. Write like a friendly strategist "
        "talking to a busy business owner — plain English, warm but direct, "
        "no jargon or abbreviations (say 'engagement rate' not 'ER', say "
        "'week-over-week growth' not 'momentum'). Every action MUST "
        "explicitly state: the platform name, the Athens time as HH:MM, the "
        "format (text / image / carousel / video), and one concrete reason "
        "grounded in the numbers. Instagram cannot publish text-only posts "
        "— for Instagram always specify image or carousel. TikTok needs "
        "video. Do not repeat the same platform+time "
        "action twice. Do not recommend posting between 00:00–06:00 Athens "
        "unless that hour's sample is explicitly strong — a thin overnight "
        "bucket is noise; prefer the platform baseline windows instead. "
        "No preamble, no closing remarks — only the numbered list."
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


async def _load_recent_posts_by_platform(
    db: AsyncSession,
    team_id: UUID,
    *,
    days: int = _RECENT_POSTS_DAYS,
    max_per_platform: int = _MAX_POSTS_PER_PLATFORM,
) -> dict[str, list[BriefPost]]:
    """Published posts in the last N days, grouped by target platform."""
    since = datetime.now(UTC) - timedelta(days=days)
    result = await db.execute(
        select(Post)
        .where(
            Post.team_id == team_id,
            Post.status == PostStatus.PUBLISHED,
            Post.published_at.is_not(None),
            Post.published_at >= since,
        )
        .options(
            selectinload(Post.targets).selectinload(PostTarget.social_account),
        )
        .order_by(Post.published_at.desc())
    )
    posts = list(result.scalars().unique().all())

    all_ids: list[UUID] = []
    for post in posts:
        all_ids.extend(post.media_ids or [])
    assets_by_id: dict[UUID, MediaAsset] = {}
    if all_ids:
        assets = (
            await db.execute(select(MediaAsset).where(MediaAsset.id.in_(all_ids)))
        ).scalars().all()
        assets_by_id = {a.id: a for a in assets}

    by_platform: dict[str, list[BriefPost]] = {}
    for post in posts:
        media = _brief_media_from_assets(post.media_ids, assets_by_id)
        missing = len(media) == 0
        preview = _preview_text(post.content_text)
        for target in post.targets or []:
            account = target.social_account
            platform = (account.platform if account else None) or ""
            if not platform:
                continue
            # Only include successfully published targets (skip failed/pending).
            if (target.status or "published") != "published":
                continue
            # TikTok MEDIA_UPLOAD stores a publish_id (v_inbox_file~/p_pub_…)
            # — the draft is in the app inbox, not live. Label, don't hide.
            pending_inbox = platform == "tiktok" and is_tiktok_publish_id(
                target.platform_post_id
            )
            bucket = by_platform.setdefault(platform, [])
            if len(bucket) >= max_per_platform:
                continue
            bucket.append(
                BriefPost(
                    post_id=str(post.id),
                    content_preview=preview,
                    published_at=target.published_at or post.published_at,
                    platform_url=target.platform_url,
                    media=list(media),
                    missing_media=missing,
                    pending_inbox=pending_inbox,
                )
            )
    return by_platform


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
    recent = await _load_recent_posts_by_platform(db, team.id)

    from app.services.growth_initiatives import initiative_summary

    initiatives = await initiative_summary(db, team.id)

    report = StrategyReport(
        generated_at=datetime.now(UTC),
        timezone=tz_name,
        team_name=team.name or "SocialAuto",
        insights=insights,
        digest=digest,
        recent_posts_by_platform=recent,
        initiatives=initiatives,
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

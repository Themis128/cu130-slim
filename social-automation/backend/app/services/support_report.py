"""Collect user + platform diagnostics for a support request and send to Slack."""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Post, PostStatus
from app.models.social_account import SocialAccount
from app.models.user import Team, User
from app.services.slack_notifications import post_support_report_to_slack

logger = logging.getLogger(__name__)


async def build_support_report_text(
    user: User,
    team: Team | None,
    db: AsyncSession,
    *,
    message: str,
    category: str,
    reply_email: str,
    ip: str | None = None,
    user_agent: str | None = None,
) -> str:
    """Build a rich Slack message with the user's message and team diagnostics."""
    now = datetime.now(UTC)
    lines: list[str] = [
        ":rotating_light: *Support request*",
        f"*Category:* {category}",
        f"*From:* {user.name or '—'} <{reply_email}>",
        f"*User ID:* {user.id}",
        f"*Team:* {team.name if team else '—'} ({team.id if team else '—'})",
        f"*Plan:* {team.plan_tier if team else '—'} · *Sub status:* {team.subscription_status if team else '—'}",
    ]
    if team and team.paddle_customer_id:
        lines.append(f"*Paddle customer:* {team.paddle_customer_id}")
    lines += [
        f"*User agent:* {user_agent or '—'}",
        f"*IP:* {ip or '—'}",
        f"*At:* {now.strftime('%a %d %b %Y %H:%M %Z')}",
        "",
        "*Message*",
        f">{message}",
        "",
    ]

    # Connected accounts
    result = await db.execute(
        select(SocialAccount)
        .where(SocialAccount.team_id == team.id)
        .order_by(SocialAccount.platform, SocialAccount.username)
    )
    accounts = result.scalars().all()
    lines.append(f"*Connected accounts ({len(accounts)})*")
    for a in accounts:
        expiry = (
            a.token_expires_at.strftime("%Y-%m-%d %H:%M")
            if a.token_expires_at
            else "—"
        )
        status = a.status or "unknown"
        name = a.display_name or a.username or a.account_id or "?"
        lines.append(
            f"  • {a.platform} — *{name}* — {status} — expires {expiry}"
        )

    # Recent posts (last 7 days)
    since = now - timedelta(days=7)
    result = await db.execute(
        select(Post)
        .where(
            Post.team_id == team.id,
            Post.created_at >= since,
        )
        .order_by(Post.created_at.desc())
    )
    posts = result.scalars().all()
    failed = [p for p in posts if p.status == PostStatus.FAILED]
    lines += [
        "",
        f"*Recent posts (7d): {len(posts)}* · failed: *{len(failed)}*",
    ]
    for p in posts[:5]:
        lines.append(f"  • {p.status} — {(p.content_text or '')[:80]} — {p.id}")
    if failed:
        lines.append("")
        lines.append("*Recent failed posts*")
        for p in failed[:5]:
            err = ""
            if p.platform_specific and isinstance(p.platform_specific, dict):
                err = str(p.platform_specific.get("error", ""))[:120]
            lines.append(f"  • {p.id} — {err or 'no error captured'}")

    return "\n".join(lines)


async def send_support_report(
    user: User,
    team: Team | None,
    db: AsyncSession,
    *,
    message: str,
    category: str,
    reply_email: str,
    ip: str | None = None,
    user_agent: str | None = None,
    post_to_slack: bool = True,
) -> dict[str, bool | str | None]:
    """Build and optionally deliver the support report to the configured Slack channel."""
    text = await build_support_report_text(
        user=user,
        team=team,
        db=db,
        message=message,
        category=category,
        reply_email=reply_email,
        ip=ip,
        user_agent=user_agent,
    )
    if not post_to_slack:
        return {"ok": True, "posted": False, "error": None, "text": text}
    ok, err = await post_support_report_to_slack(text)
    if not ok:
        logger.warning("Support report to Slack failed: %s", err)
    return {"ok": True, "posted": ok, "error": err}

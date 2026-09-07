"""Transactional email templates for SocialAuto.

Built on top of the existing ``send_email()`` from ``email_digest.py``.
Each function builds a subject + plain-text + HTML body and calls
``send_email()`` with the recipient's address.

All sends are logged to the ``email_logs`` table via ``_log_email()``.

Notification preferences are checked by the *caller*, not inside these
functions — see the call sites in ``auth.py``, ``teams.py``, ``deps.py``,
and ``publishing.py`` for the preference gates:
  - ``email_new_post`` → post_published (in publishing.py Celery task)
  - ``email_on_quota`` → quota_warning (in deps.py check_quota)
  - ``email_account_connected`` → account_connected (in auth.py OAuth callback)
  - welcome, password_reset, team_invite: always sent (no preference gate)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.email_digest import send_email

if TYPE_CHECKING:
    from app.models.content import Post
    from app.models.user import User

logger = logging.getLogger(__name__)

FROM_NAME = "SocialAuto"


async def _log_email(
    recipient: str,
    subject: str,
    template: str,
    user_id: object | None = None,
    team_id: object | None = None,
    status: str = "sent",
    error: str | None = None,
) -> None:
    """Persist a row to email_logs for delivery audit. Non-fatal."""
    try:
        from app.db.session import async_session_maker
        from app.models.email_log import EmailLog

        async with async_session_maker() as session:
            session.add(
                EmailLog(
                    recipient=recipient,
                    subject=subject[:500],
                    template=template,
                    status=status,
                    error=error,
                    user_id=user_id,
                    team_id=team_id,
                )
            )
            await session.commit()
    except Exception:
        logger.debug("email_log insert failed (non-fatal)", exc_info=True)


def _html_wrapper(title: str, body_html: str) -> str:
    """Wrap content in a simple responsive HTML email template."""
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<style>
  body {{ margin:0; padding:0; background:#0f172a; font-family:Inter,sans-serif; color:#e2e8f0; }}
  .container {{ max-width:600px; margin:0 auto; padding:32px 24px; }}
  .header {{ text-align:center; margin-bottom:32px; }}
  .logo {{ font-size:24px; font-weight:700; color:#38bdf8; }}
  .card {{ background:#1e293b; border-radius:12px; padding:32px; margin-bottom:24px; }}
  .btn {{ display:inline-block; background:#38bdf8; color:#0f172a; font-weight:600;
          padding:12px 32px; border-radius:8px; text-decoration:none; margin:16px 0; }}
  .footer {{ text-align:center; font-size:13px; color:#64748b; margin-top:24px; }}
  h1 {{ font-size:22px; margin:0 0 16px; }}
  p {{ line-height:1.6; margin:0 0 12px; }}
  ul {{ line-height:1.8; }}
</style>
</head>
<body>
<div class="container">
  <div class="header"><div class="logo">SocialAuto</div></div>
  <div class="card">
    <h1>{title}</h1>
    {body_html}
  </div>
  <div class="footer">
    <p>SocialAuto — Social Media Automation Platform</p>
    <p>cloudless.gr</p>
  </div>
</div>
</body>
</html>"""


def _user_email(user: User) -> str:
    return user.email


# ── Welcome ─────────────────────────────────────────────────────────────────

async def send_welcome_email(user: User) -> None:
    """Send a welcome email to a newly registered user."""
    subject = f"Welcome to SocialAuto, {user.name or 'there'}!"
    name = user.name or user.email.split("@")[0]
    text = (
        f"Welcome to SocialAuto, {name}!\n\n"
        "You're all set to automate your social media with AI-powered workflows.\n\n"
        "Here's what you can do next:\n"
        "1. Connect your social accounts (LinkedIn, Facebook, Instagram, Twitter/X, TikTok)\n"
        "2. Set up your brand identity\n"
        "3. Create your first AI-powered post\n"
        "4. Schedule posts across all platforms\n\n"
        "Get started: https://social.cloudless.gr/dashboard\n\n"
        "Need help? Reply to this email.\n\n"
        "— The SocialAuto Team"
    )
    html = _html_wrapper(
        f"Welcome, {name}!",
        """
        <p>You're all set to automate your social media with AI-powered workflows.</p>
        <p><strong>Here's what you can do next:</strong></p>
        <ul>
          <li>Connect your social accounts (LinkedIn, Facebook, Instagram, Twitter/X, TikTok)</li>
          <li>Set up your brand identity</li>
          <li>Create your first AI-powered post</li>
          <li>Schedule posts across all platforms</li>
        </ul>
        <a href="https://social.cloudless.gr/dashboard" class="btn">Get Started</a>
        <p>Need help? Just reply to this email.</p>
        """,
    )
    try:
        await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[_user_email(user)])
        await _log_email(_user_email(user), subject, "welcome", user_id=user.id)
    except Exception as exc:
        logger.exception("Failed to send welcome email to %s", user.email)
        await _log_email(_user_email(user), subject, "welcome", user_id=user.id, status="failed", error=str(exc))


# ── Password Reset ──────────────────────────────────────────────────────────

async def send_password_reset_email(user: User, reset_link: str) -> None:
    """Send a password reset email with a reset link."""
    subject = "Reset your SocialAuto password"
    text = (
        f"Hi {user.name or user.email},\n\n"
        "We received a request to reset your password.\n\n"
        f"Click the link below to set a new password:\n{reset_link}\n\n"
        "This link expires in 30 minutes.\n\n"
        "If you didn't request this, you can safely ignore this email.\n\n"
        "— The SocialAuto Team"
    )
    html = _html_wrapper(
        "Reset your password",
        f"""
        <p>We received a request to reset your password.</p>
        <p>Click the button below to set a new password:</p>
        <a href="{reset_link}" class="btn">Reset Password</a>
        <p>This link expires in 30 minutes.</p>
        <p style="color:#64748b;font-size:13px;">If you didn't request this, you can safely ignore this email.</p>
        """,
    )
    try:
        await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[_user_email(user)])
        await _log_email(_user_email(user), subject, "password_reset", user_id=user.id)
    except Exception as exc:
        logger.exception("Failed to send password reset email to %s", user.email)
        await _log_email(_user_email(user), subject, "password_reset", user_id=user.id, status="failed", error=str(exc))


# ── Post Published ──────────────────────────────────────────────────────────

async def send_post_published_email(user: User, post: Post) -> None:
    """Send a notification when a scheduled post is published."""
    platforms = ", ".join(
        t.platform for t in getattr(post, "targets", []) if t.platform
    ) or "your connected accounts"
    snippet = (post.content_text or "")[:100]
    if len(post.content_text or "") > 100:
        snippet += "..."

    subject = f"Your post has been published to {platforms}"
    text = (
        f"Hi {user.name or user.email},\n\n"
        f"Your post has been successfully published to {platforms}.\n\n"
        f"Post preview:\n\"{snippet}\"\n\n"
        "View your analytics: https://social.cloudless.gr/analytics\n\n"
        "— The SocialAuto Team"
    )
    html = _html_wrapper(
        "Post published!",
        f"""
        <p>Your post has been successfully published to <strong>{platforms}</strong>.</p>
        <p><strong>Post preview:</strong></p>
        <p style="background:#0f172a;padding:16px;border-radius:8px;border-left:3px solid #38bdf8;">{snippet}</p>
        <a href="https://social.cloudless.gr/analytics" class="btn">View Analytics</a>
        """,
    )
    try:
        await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[_user_email(user)])
        team_id = getattr(post, "team_id", None)
        await _log_email(_user_email(user), subject, "post_published", user_id=user.id, team_id=team_id)
    except Exception as exc:
        logger.exception("Failed to send post-published email to %s", user.email)
        await _log_email(_user_email(user), subject, "post_published", user_id=user.id, status="failed", error=str(exc))


# ── Account Connected ───────────────────────────────────────────────────────

async def send_account_connected_email(user: User, platform: str) -> None:
    """Send a notification when a social account is connected via OAuth."""
    subject = f"Your {platform} account is connected"
    text = (
        f"Hi {user.name or user.email},\n\n"
        f"Your {platform} account has been successfully connected to SocialAuto.\n\n"
        "You can now create and schedule posts for this platform.\n\n"
        "Create a post: https://social.cloudless.gr/content/new\n\n"
        "— The SocialAuto Team"
    )
    html = _html_wrapper(
        f"{platform} connected!",
        f"""
        <p>Your <strong>{platform}</strong> account has been successfully connected to SocialAuto.</p>
        <p>You can now create and schedule posts for this platform.</p>
        <a href="https://social.cloudless.gr/content/new" class="btn">Create a Post</a>
        """,
    )
    try:
        await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[_user_email(user)])
        await _log_email(_user_email(user), subject, "account_connected", user_id=user.id)
    except Exception as exc:
        logger.exception("Failed to send account-connected email to %s", user.email)
        await _log_email(_user_email(user), subject, "account_connected", user_id=user.id, status="failed", error=str(exc))


# ── Quota Warning ───────────────────────────────────────────────────────────

async def send_quota_warning_email(
    user: User,
    resource: str,
    used: int,
    limit: int,
) -> None:
    """Send a warning when a team approaches their plan limit (80%)."""
    pct = int((used / limit) * 100) if limit > 0 else 0
    subject = f"You've used {pct}% of your {resource.replace('_', ' ')} limit"
    text = (
        f"Hi {user.name or user.email},\n\n"
        f"You've used {used} out of {limit} {resource.replace('_', ' ')} this month.\n\n"
        "To avoid interruptions, consider upgrading your plan.\n\n"
        "View plans: https://social.cloudless.gr/pricing\n\n"
        "— The SocialAuto Team"
    )
    html = _html_wrapper(
        f"{pct}% of limit used",
        f"""
        <p>You've used <strong>{used}</strong> out of <strong>{limit}</strong> {resource.replace('_', ' ')} this month.</p>
        <div style="background:#0f172a;border-radius:8px;padding:4px;margin:16px 0;">
          <div style="background:#38bdf8;height:8px;border-radius:4px;width:{pct}%;"></div>
        </div>
        <p>To avoid interruptions, consider upgrading your plan.</p>
        <a href="https://social.cloudless.gr/pricing" class="btn">View Plans</a>
        """,
    )
    try:
        await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[_user_email(user)])
        await _log_email(_user_email(user), subject, "quota_warning", user_id=user.id)
    except Exception as exc:
        logger.exception("Failed to send quota warning email to %s", user.email)
        await _log_email(_user_email(user), subject, "quota_warning", user_id=user.id, status="failed", error=str(exc))


# ── Team Invite ─────────────────────────────────────────────────────────────

async def send_team_invite_email(
    inviter_name: str,
    invitee_email: str,
    team_name: str,
    invite_link: str,
) -> None:
    """Send a team invitation email."""
    subject = f"{inviter_name} invited you to join {team_name} on SocialAuto"
    text = (
        f"You've been invited by {inviter_name} to join the team \"{team_name}\" on SocialAuto.\n\n"
        "SocialAuto is a social media automation platform with AI-powered content generation, "
        "multi-platform publishing, and analytics.\n\n"
        f"Click the link below to accept the invitation:\n{invite_link}\n\n"
        "— The SocialAuto Team"
    )
    html = _html_wrapper(
        f"Join {team_name}",
        f"""
        <p>You've been invited by <strong>{inviter_name}</strong> to join the team
        <strong>{team_name}</strong> on SocialAuto.</p>
        <p>SocialAuto is a social media automation platform with AI-powered content generation,
        multi-platform publishing, and analytics.</p>
        <a href="{invite_link}" class="btn">Accept Invitation</a>
        """,
    )
    try:
        await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[invitee_email])
        await _log_email(invitee_email, subject, "team_invite")
    except Exception as exc:
        logger.exception("Failed to send team invite email to %s", invitee_email)
        await _log_email(invitee_email, subject, "team_invite", status="failed", error=str(exc))


async def send_team_added_email(
    adder_name: str,
    user_email: str,
    team_name: str,
    dashboard_link: str,
) -> None:
    """Send a 'you have been added to a team' notification.

    Unlike :func:`send_team_invite_email`, this is sent to an existing user
    who has already been added as a member — there is nothing to accept.
    """
    subject = f"You have been added to {team_name} on SocialAuto"
    text = (
        f"Hi,\n\n"
        f"{adder_name} has added you to the team \"{team_name}\" on SocialAuto.\n\n"
        f"You can access the team dashboard here:\n{dashboard_link}\n\n"
        "— The SocialAuto Team"
    )
    html = _html_wrapper(
        f"Joined {team_name}",
        f"""
        <p><strong>{adder_name}</strong> has added you to the team
        <strong>{team_name}</strong> on SocialAuto.</p>
        <a href="{dashboard_link}" class="btn">Go to Dashboard</a>
        """,
    )
    try:
        await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[user_email])
        await _log_email(user_email, subject, "team_added")
    except Exception as exc:
        logger.exception("Failed to send team-added email to %s", user_email)
        await _log_email(user_email, subject, "team_added", status="failed", error=str(exc))


async def send_instagram_session_alert_email(
    owner_email: str,
    owner_name: str,
    account_username: str,
    reason: str,
) -> None:
    """Send an alert that an Instagram private-API session has expired."""
    subject = f"Instagram session expired for @{account_username}"
    text = (
        f"Hi {owner_name or owner_email},\n\n"
        f"The Instagram private-API session for @{account_username} has expired.\n\n"
        f"Reason: {reason}\n\n"
        "To restore it:\n"
        "1. Log in to Instagram in a real browser.\n"
        "2. Copy the `sessionid` cookie from devtools.\n"
        "3. Import it: .devin/skills/instagram-private-api/scripts/import-session.sh <sessionid>\n\n"
        "— SocialAuto"
    )
    html = _html_wrapper(
        "Instagram session expired",
        f"""
        <p>Hi <strong>{owner_name or owner_email}</strong>,</p>
        <p>The Instagram private-API session for <strong>@{account_username}</strong>
        has expired.</p>
        <p><em>Reason: {reason}</em></p>
        <p>To restore it:</p>
        <ol>
          <li>Log in to Instagram in a real browser.</li>
          <li>Copy the <code>sessionid</code> cookie from devtools.</li>
          <li>Import it via
            <code>.devin/skills/instagram-private-api/scripts/import-session.sh &lt;sessionid&gt;</code>
          </li>
        </ol>
        """,
    )
    try:
        await send_email(subject=subject, text_body=text, html_body=html, to_addrs=[owner_email])
        await _log_email(owner_email, subject, "instagram_session_alert")
    except Exception as exc:
        logger.exception("Failed to send IG session alert to %s", owner_email)
        await _log_email(owner_email, subject, "instagram_session_alert", status="failed", error=str(exc))

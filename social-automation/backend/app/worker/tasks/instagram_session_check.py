"""Celery task — check Instagram private-API sidecar session health.

Runs every 6 hours. ``business_login`` accounts are validated against the
Graph API with their OAuth token (the private-API sidecar is only a
fallback for those). Other accounts with a ``private_api_session_id`` in
``meta_data`` are checked via the aiograpi-rest sidecar's ``GET /account``
endpoint. If the relevant session is expired or unreachable, the account
status is set to ``expired`` and an alert email is sent to the team owner.

The task is non-fatal — a single account failure does not abort the loop.
"""

import asyncio
import logging
import re
from datetime import UTC, datetime

import httpx
from celery import shared_task
from sqlalchemy import select

from app.core.config import get_settings
from app.core.security import decrypt_field, decrypt_token
from app.models.social_account import SocialAccount
from app.models.user import Team, User
from app.services.slack_notifications import post_alert_to_slack, session_heal_buttons
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

# Alert cooldown — don't email the same owner more than once per day per account.
_ALERT_COOLDOWN_HOURS = 24

# Delay before the confirmation re-probe on a negative result.
_RECHECK_DELAY_S = 10.0

# OAuthException codes that mean the token itself is invalid/expired.
_GRAPH_TOKEN_DEAD_CODES = frozenset({190, 102})

_SIDECAR_LOGIN_REQUIRED = re.compile(r"LoginRequired|login_required|Session not found|please relogin", re.I)


_worker_db = task_session


async def _check_sidecar_session(session_id: str, sidecar_url: str) -> bool | None:
    """Return True/False on a definitive answer, None when unreachable.

    A transport failure (DNS blip, sidecar down) is NOT proof the session
    died — callers must treat None as inconclusive, never as expired.
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{sidecar_url.rstrip('/')}/account",
                headers={"X-Session-ID": session_id},
            )
    except Exception as exc:
        logger.warning("Instagram sidecar unreachable: %s", exc)
        return None
    if resp.status_code == 200:
        return True
    if resp.status_code in (400, 401, 403):
        return False
    if resp.status_code >= 500 and _SIDECAR_LOGIN_REQUIRED.search(resp.text or ""):
        # aiograpi-rest surfaces LoginRequired / a missing session as a 500.
        return False
    return None


def _graph_verdict(resp: httpx.Response) -> bool | None:
    """Classify a graph.instagram.com response.

    Meta returns throttling (codes 4/17/32/613) and transient errors (1/2,
    ``is_transient``) as 4xx too, so only OAuthException 190/102 proves
    the token is dead. Anything else, including an unparseable body, is
    inconclusive.
    """
    if resp.status_code == 200:
        return True
    if resp.status_code not in (400, 401, 403):
        return None
    try:
        err = resp.json().get("error") or {}
    except (ValueError, AttributeError):
        return None
    if not isinstance(err, dict) or err.get("is_transient"):
        return None
    return False if err.get("code") in _GRAPH_TOKEN_DEAD_CODES else None


async def _check_sidecar_health(sidecar_url: str) -> bool:
    """Return True if the sidecar process itself is up."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{sidecar_url.rstrip('/')}/health")
            return resp.status_code == 200
    except Exception:
        return False


async def _check_graph_token(account: SocialAccount) -> bool | None:
    """Return True if the account's Business Login OAuth token is valid.

    ``business_login`` accounts publish via graph.instagram.com with their
    OAuth access token — the private-API sidecar session is only a fallback,
    so probing the sidecar would wrongly flag a healthy account as expired.
    A cheap ``/me`` read is enough to prove the token works.
    """
    try:
        token = decrypt_token(account.access_token_enc)
    except Exception:
        return False
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                "https://graph.instagram.com/v26.0/me",
                params={"fields": "id", "access_token": token},
            )
    except Exception as exc:
        logger.warning("IG Graph token check failed for @%s: %s", account.username, exc)
        return None
    return _graph_verdict(resp)


async def _evaluate(account: SocialAccount, session_id: str, sidecar_url: str) -> bool | None:
    """Combine the Graph-token and sidecar probes into one verdict.

    Returns True (healthy), False (definitively rejected), or None
    (inconclusive — transport failure / nothing to probe). ``business_login``
    accounts check the Graph token first and fall back to the sidecar
    session; the account is only expired when a lane definitively rejects
    and no fallback can confirm health.
    """
    meta = account.meta_data or {}
    if meta.get("login_type") == "business_login":
        graph = await _check_graph_token(account)
        if graph is True:
            return True
        if not session_id:
            return graph
        sidecar = await _check_sidecar_session(session_id, sidecar_url)
        if sidecar is True:
            return True
        if graph is None:
            # Only a definitive Graph rejection can expire a business_login
            # account — the sidecar session is a fallback, not the publish lane.
            return None
        return False if sidecar is False else None
    if session_id:
        return await _check_sidecar_session(session_id, sidecar_url)
    return None


async def _send_alert(owner: User, account_username: str, reason: str) -> None:
    """Send an alert email about an expired Instagram session. Cooldown 24h."""
    try:
        from app.models.email_log import EmailLog

        # Check cooldown — skip if we already alerted in the last 24h.
        async with task_session() as db:
            from datetime import timedelta

            cutoff = datetime.now(UTC) - timedelta(hours=_ALERT_COOLDOWN_HOURS)
            from sqlalchemy import func

            recent = await db.execute(
                select(func.count(EmailLog.id)).where(
                    EmailLog.recipient == owner.email,
                    EmailLog.template == "instagram_session_alert",
                    EmailLog.created_at >= cutoff,
                )
            )
            if recent.scalar_one() > 0:
                logger.info("Skipping IG alert for %s — within cooldown", owner.email)
                return

        # Slack alert (best-effort; uses same cooldown gating as email).
        alert_text = "\n".join(
            [
                "*Instagram needs you to reconnect*",
                f"• Account: `@{account_username}`",
                "• What to do next: go to Settings → Accounts and re-connect Instagram.",
                f"• What happened: {(reason or '').replace(chr(10), ' ').strip()[:300] or 'Session check failed'}",
                "_Cloudless · Clear skies. Zero friction._",
            ]
        )[:2000]
        await post_alert_to_slack(
            alert_text,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": alert_text}}] + session_heal_buttons(),
        )

        from app.services.email_templates import send_instagram_session_alert_email

        await send_instagram_session_alert_email(
            owner_email=owner.email,
            owner_name=owner.name or "",
            account_username=account_username,
            reason=reason,
        )
    except Exception:
        logger.debug("IG alert email failed (non-fatal)", exc_info=True)


async def _run_check() -> dict:
    """Check all Instagram sessions and return a summary."""
    settings = get_settings()
    sidecar_url = settings.INSTAGRAM_PRIVATE_API_URL
    summary = {"checked": 0, "healthy": 0, "expired": 0, "sidecar_up": False}

    summary["sidecar_up"] = await _check_sidecar_health(sidecar_url)
    if not summary["sidecar_up"]:
        logger.error("Instagram sidecar at %s is not responding", sidecar_url)

    async with _worker_db() as db:
        result = await db.execute(select(SocialAccount).where(SocialAccount.platform == "instagram"))
        accounts = result.scalars().all()

        for account in accounts:
            meta = account.meta_data or {}
            session_id_enc = meta.get("private_api_session_id")
            session_id = (decrypt_field(session_id_enc) or "") if session_id_enc else ""

            healthy = await _evaluate(account, session_id, sidecar_url)
            if healthy is False:
                # Confirmation pass — a single transport blip must never flip
                # a live account to expired (2026-11-22 incident: worker DNS
                # failure expired all three IG rows while tokens were valid).
                await asyncio.sleep(_RECHECK_DELAY_S)
                # Reload so a reconnect committed during the delay is what
                # gets re-probed, not the stale credentials.
                await db.refresh(account)
                meta = account.meta_data or {}
                session_id_enc = meta.get("private_api_session_id")
                session_id = (decrypt_field(session_id_enc) or "") if session_id_enc else ""
                healthy = await _evaluate(account, session_id, sidecar_url)
            if healthy is None:
                continue

            summary["checked"] += 1
            if healthy:
                summary["healthy"] += 1
                if account.status == "expired":
                    account.status = "active"
                    await db.commit()
                logger.info("IG session for @%s is healthy", account.username)
            else:
                summary["expired"] += 1
                if account.status != "expired":
                    account.status = "expired"
                    await db.commit()
                logger.warning("IG session for @%s is expired", account.username)

                # Alert the team owner.
                owner_result = await db.execute(select(User).join(Team, Team.owner_id == User.id).where(Team.id == account.team_id))
                owner = owner_result.scalar_one_or_none()
                if owner is not None:
                    if meta.get("login_type") == "business_login":
                        reason = "OAuth token rejected by Instagram Graph API"
                    else:
                        reason = "sidecar unreachable" if not summary["sidecar_up"] else "session rejected"
                    await _send_alert(owner, account.username or "unknown", reason)

    logger.info("Instagram session check complete: %s", summary)
    return summary


@shared_task(name="app.worker.tasks.instagram_session_check.check_instagram_sessions")
def check_instagram_sessions() -> dict:
    """Periodic task — check all Instagram sidecar sessions (every 6h)."""
    return run_async(_run_check())

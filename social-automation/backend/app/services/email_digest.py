"""Email delivery for SocialAuto digests.

No Cloudflare Email Sending (paid). No AWS SES.

Providers:
  smtp (preferred, free) — Resend SMTP relay already used by omv-ha mail
    (smtp.resend.com:587). Delivers to tbaltzakis@cloudless.gr via CF Email
    Routing → mail-ingest → dovecot, so the dedicated mail client sees it.

  local — host Postfix via Docker gateway :25 (often lands in WSL
    /var/mail only; not the omv-ha mailbox).

  cloudflare — requires paid Workers Email Sending; keep unused.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import TYPE_CHECKING

import httpx

from app.core.config import get_settings

if TYPE_CHECKING:
    from app.services.slack_digest import DigestReport

logger = logging.getLogger(__name__)

CF_EMAIL_SEND_URL = (
    "https://api.cloudflare.com/client/v4/accounts/{account_id}/email/sending/send"
)
CF_SMTP_HOST = "smtp.mx.cloudflare.net"
CF_SMTP_PORT = 465


def _email_api_token(settings) -> str:
    return (
        (settings.CLOUDFLARE_EMAIL_API_TOKEN or "").strip()
        or (settings.CLOUDFLARE_API_TOKEN or "").strip()
    )


def _html_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def digest_email_subject(report: DigestReport) -> str:
    errors = sum(1 for i in report.issues if i.severity == "error")
    warnings = sum(1 for i in report.issues if i.severity == "warning")
    day = report.generated_at.strftime("%Y-%m-%d")
    if errors:
        return f"[SocialAuto] ERRORS ({errors}) — daily report {day}"
    if warnings:
        return f"[SocialAuto] Warnings ({warnings}) — daily report {day}"
    return f"[SocialAuto] Daily report {day} — OK"


def digest_to_plaintext(report: DigestReport) -> str:
    md = report.to_slack_markdown()
    return (
        md.replace("*", "")
        .replace("🟢", "")
        .replace("❌", "[ERROR]")
        .replace("⚠️", "[WARN]")
    )


def digest_to_html(report: DigestReport) -> str:
    o = report.overview
    errors = [i for i in report.issues if i.severity == "error"]
    warnings = [i for i in report.issues if i.severity == "warning"]

    rows = "".join(
        f"<tr><td>{_html_escape(k.replace('_', ' ').title())}</td>"
        f"<td><strong>{_html_escape(str(v))}</strong></td></tr>"
        for k, v in {
            "total_posts": o.get("total_posts", 0),
            "published": o.get("published_posts", 0),
            "scheduled": o.get("scheduled_posts", 0),
            "drafts": o.get("draft_posts", 0),
            "failed": o.get("failed_posts", 0),
            "impressions_24h": report.impressions_24h,
            "engagement_24h": report.engagement_24h,
            "connected_accounts": o.get("connected_accounts", 0),
        }.items()
    )

    top = ""
    if report.top_posts:
        items = "".join(
            f"<li>eng <b>{p.get('engagement', 0)}</b> · "
            f"imp <b>{p.get('impressions', 0)}</b> — "
            f"{_html_escape(str(p.get('snippet') or p.get('platform_post_id') or ''))}</li>"
            for p in report.top_posts[:5]
        )
        top = f"<h3>Top posts</h3><ol>{items}</ol>"

    issue_html = "<p><strong>Issues:</strong> none</p>"
    if errors or warnings:
        parts: list[str] = []
        if errors:
            parts.append("<h3 style='color:#b91c1c'>Errors</h3><ul>")
            for i in errors:
                parts.append(
                    f"<li><b>{_html_escape(i.title)}</b>"
                    f"{(' — ' + _html_escape(i.detail)) if i.detail else ''}</li>"
                )
            parts.append("</ul>")
        if warnings:
            parts.append("<h3 style='color:#a16207'>Warnings</h3><ul>")
            for i in warnings:
                parts.append(
                    f"<li><b>{_html_escape(i.title)}</b>"
                    f"{(' — ' + _html_escape(i.detail)) if i.detail else ''}</li>"
                )
            parts.append("</ul>")
        issue_html = "".join(parts)

    return f"""<!DOCTYPE html>
<html><body style="font-family:system-ui,sans-serif;line-height:1.45;color:#111">
  <h2>SocialAuto daily report · {_html_escape(report.team_name)}</h2>
  <p>{_html_escape(report.generated_at.isoformat())} · last {report.days} day(s)</p>
  <h3>Analytics</h3>
  <table cellpadding="6" cellspacing="0" border="1" style="border-collapse:collapse">
    {rows}
  </table>
  {top}
  {issue_html}
  <p style="color:#666;font-size:12px">Also posted to Slack #socialauto · cloudless.gr Social Automation</p>
</body></html>
"""


def _recipients(settings) -> list[str]:
    return [
        a.strip()
        for a in (settings.DIGEST_EMAIL_TO or "").split(",")
        if a.strip()
    ]


def send_email_smtp(
    *,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    to_addrs: list[str] | None = None,
) -> None:
    """Send via local/host SMTP (free Postfix on :25)."""
    settings = get_settings()
    host = (settings.SMTP_HOST or "").strip()
    port = int(settings.SMTP_PORT or 25)
    user = (settings.SMTP_USER or "").strip()
    password = (settings.SMTP_PASSWORD or "").strip()
    from_addr = (settings.SMTP_FROM or "noreply@cloudless.gr").strip()
    recipients = to_addrs or _recipients(settings)

    if not host or not from_addr or not recipients:
        raise RuntimeError(
            "SMTP not configured. Set SMTP_HOST, SMTP_FROM, DIGEST_EMAIL_TO "
            "(local Postfix: SMTP_HOST=host.docker.internal SMTP_PORT=25)"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"SocialAuto <{from_addr}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    if not getattr(settings, "SMTP_SSL_VERIFY", True):
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=45, context=context) as smtp:
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=45) as smtp:
            smtp.ehlo()
            # Local Postfix :25 typically has no STARTTLS; optional for 587
            if settings.SMTP_USE_TLS and smtp.has_extn("starttls"):
                smtp.starttls(context=context)
                smtp.ehlo()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)


def send_email_cloudflare_smtp(
    *,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    to_addrs: list[str] | None = None,
) -> None:
    """CF Email Sending over authenticated SMTP (smtp.mx.cloudflare.net:465)."""
    settings = get_settings()
    token = _email_api_token(settings)
    from_addr = (settings.SMTP_FROM or "noreply@cloudless.gr").strip()
    recipients = to_addrs or _recipients(settings)
    if not token or not from_addr or not recipients:
        raise RuntimeError(
            "Cloudflare SMTP needs CLOUDFLARE_EMAIL_API_TOKEN (Email Sending Edit), "
            "SMTP_FROM, and DIGEST_EMAIL_TO"
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"SocialAuto <{from_addr}>"
    msg["To"] = ", ".join(recipients)
    msg.set_content(text_body)
    if html_body:
        msg.add_alternative(html_body, subtype="html")

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(CF_SMTP_HOST, CF_SMTP_PORT, timeout=45, context=context) as smtp:
        smtp.login("api_token", token)
        smtp.send_message(msg)


async def send_email_cloudflare_verified(
    *,
    subject: str,
    text_body: str,
    html_body: str,
    to_addrs: list[str] | None = None,
) -> None:
    """Free Cloudflare Email Sending to verified Email Routing destinations only."""
    settings = get_settings()
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = _email_api_token(settings)
    from_addr = (settings.SMTP_FROM or "noreply@cloudless.gr").strip()
    recipients = to_addrs or _recipients(settings)
    if not account_id or not token or not recipients:
        raise RuntimeError(
            "Cloudflare send needs CLOUDFLARE_ACCOUNT_ID, "
            "CLOUDFLARE_EMAIL_API_TOKEN (Email Sending Edit), and DIGEST_EMAIL_TO"
        )

    url = CF_EMAIL_SEND_URL.format(account_id=account_id)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    rest_error: str | None = None
    async with httpx.AsyncClient(timeout=60.0) as client:
        for to in recipients:
            resp = await client.post(
                url,
                headers=headers,
                json={
                    "to": to,
                    "from": f"SocialAuto <{from_addr}>",
                    "subject": subject,
                    "html": html_body,
                    "text": text_body,
                },
            )
            data = resp.json() if resp.content else {}
            if resp.status_code < 300 and data.get("success"):
                continue
            err = (data.get("errors") or [{}])[0].get("message") or resp.text[:200]
            rest_error = f"Cloudflare Email HTTP {resp.status_code}: {err}"
            logger.warning("CF Email REST failed (%s); trying SMTP submit", rest_error)
            break
        else:
            return

    # SMTP submit is reachable from this WSL host even when REST token scopes differ
    try:
        await asyncio.to_thread(
            send_email_cloudflare_smtp,
            subject=subject,
            text_body=text_body,
            html_body=html_body,
            to_addrs=to_addrs,
        )
    except Exception as smtp_exc:  # noqa: BLE001
        raise RuntimeError(
            f"{rest_error}; SMTP fallback also failed: {smtp_exc}. "
            "Create a Cloudflare API token with Account → Email Sending → Edit, "
            "set CLOUDFLARE_EMAIL_API_TOKEN, EMAIL_PROVIDER=cloudflare, then recreate "
            "social-api/social-worker. DIGEST_EMAIL_TO must be a verified Email Routing "
            "destination (free) or an onboarded sending-domain recipient."
        ) from smtp_exc


async def send_email(
    *,
    subject: str,
    text_body: str,
    html_body: str | None = None,
    to_addrs: list[str] | None = None,
) -> None:
    settings = get_settings()
    html = html_body or f"<pre>{_html_escape(text_body)}</pre>"
    provider = (settings.EMAIL_PROVIDER or "local").strip().lower()

    if provider in {"cloudflare", "cf"}:
        await send_email_cloudflare_verified(
            subject=subject,
            text_body=text_body,
            html_body=html,
            to_addrs=to_addrs,
        )
        return

    await asyncio.to_thread(
        send_email_smtp,
        subject=subject,
        text_body=text_body,
        html_body=html,
        to_addrs=to_addrs,
    )


async def email_digest(report: DigestReport) -> DigestReport:
    settings = get_settings()
    if not (settings.DIGEST_EMAIL_TO or "").strip():
        report.email_error = "DIGEST_EMAIL_TO not set"
        return report

    if settings.DIGEST_EMAIL_ISSUES_ONLY and not report.issues:
        report.email_error = "skipped (no issues; DIGEST_EMAIL_ISSUES_ONLY=true)"
        return report

    provider = (settings.EMAIL_PROVIDER or "local").strip().lower()
    if provider == "local" and not (settings.SMTP_HOST or "").strip():
        report.email_error = "SMTP_HOST not set for free local Postfix"
        return report

    try:
        await send_email(
            subject=digest_email_subject(report),
            text_body=digest_to_plaintext(report),
            html_body=digest_to_html(report),
        )
        report.emailed = True
    except Exception as exc:  # noqa: BLE001
        report.email_error = str(exc) or repr(exc)
        logger.exception("Failed to email digest")
    return report

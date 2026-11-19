"""LinkedIn Ads campaign control — pause/resume ad campaigns.

Primary path is the Marketing API (``rw_ads`` granted 2026-11-16,
Advertising API Development Tier approved 2026-11-18): read +
``PARTIAL_UPDATE`` on ``/rest/adAccounts/{acct}/adCampaigns/{id}``.
The browser-sidecar Campaign Manager scrape remains as fallback for
transport/5xx failures — it needs a logged-in sidecar session and is
the brittle lane this replaces.

NOTE: Development Tier only manages ad accounts allowlisted in the
developer portal (Products → Advertising API → View settings → Ad
Accounts, max 5). An unlisted account returns 403 FORBIDDEN — that is
app configuration, not a token problem.

Triggered by Slack interactive buttons / slash commands via
``app.api.slack``.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

import httpx

from app.core.config import get_settings
from app.services.slack_notifications import _post_slack_text

logger = logging.getLogger(__name__)

_CM_BASE = "https://www.linkedin.com/campaignmanager"
_REST = "https://api.linkedin.com/rest"
_LI_VERSION = "202610"


async def _api_token(db=None) -> str:
    """Decrypt the first active LinkedIn account token (member grant —
    org rows store the same token). ``db`` lets worker callers pass their
    own session (async_session_maker pools are unsafe across worker loops).
    """
    from sqlalchemy import select

    from app.core.security import decrypt_token
    from app.db.session import async_session_maker
    from app.models.social_account import SocialAccount

    async def _lookup(s):
        return (
            (
                await s.execute(
                    select(SocialAccount)
                    .where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.status == "active",
                    )
                    .limit(1)
                )
            )
            .scalars()
            .first()
        )

    if db is not None:
        acc = await _lookup(db)
    else:
        async with async_session_maker() as s:
            acc = await _lookup(s)
    if not acc or not acc.access_token_enc:
        raise RuntimeError("no active LinkedIn account token")
    return decrypt_token(bytes(acc.access_token_enc))


def _api_headers(token: str, method: str | None = None) -> dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "LinkedIn-Version": _LI_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }
    if method:
        h["X-RestLi-Method"] = method
    return h


async def _api_set_status(action: str, campaign_id: str, acct_id: str, db=None) -> dict[str, Any] | None:
    """Marketing API pause/resume. Returns None when the API path can't
    decide (transport/5xx) so the caller can fall back to the sidecar;
    a dict means the API answered definitively (incl. config 403)."""
    want = "PAUSED" if action == "pause" else "ACTIVE"
    url = f"{_REST}/adAccounts/{acct_id}/adCampaigns/{campaign_id}"
    try:
        token = await _api_token(db)
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(url, headers=_api_headers(token))
            if r.status_code == 403:
                return {
                    "ok": False,
                    "status": "forbidden",
                    "detail": "App not allowlisted for this ad account — add it in the dev portal (Products → Advertising API → View settings → Ad Accounts)",
                }
            if r.status_code != 200:
                logger.warning("ads-control API status read HTTP %s: %s", r.status_code, r.text[:200])
                return None
            current = str((r.json() or {}).get("status") or "").upper()
            if current in ("COMPLETED", "ARCHIVED"):
                return {
                    "ok": False,
                    "status": current.lower(),
                    "detail": f"Campaign is {current.lower()} — cannot pause/resume",
                }
            if current == want:
                return {"ok": True, "status": current.lower(), "detail": f"Already {want.lower()}"}
            if not current:
                return {"ok": False, "status": "unknown", "detail": "Campaign status unreadable via API"}

            w = await client.post(
                url,
                headers={**_api_headers(token, "PARTIAL_UPDATE"), "Content-Type": "application/json"},
                json={"patch": {"$set": {"status": want}}},
            )
            if w.status_code == 403:
                return {
                    "ok": False,
                    "status": "forbidden",
                    "detail": "App not allowlisted for this ad account — add it in the dev portal (Products → Advertising API → View settings → Ad Accounts)",
                }
            if w.status_code >= 500:
                logger.warning("ads-control API write HTTP %s: %s", w.status_code, w.text[:200])
                return None
            if w.status_code not in (200, 201, 204):
                return {
                    "ok": False,
                    "status": "unknown",
                    "detail": f"API write HTTP {w.status_code}: {w.text[:200]}",
                }

            # Confirm the flip landed.
            v = await client.get(url, headers=_api_headers(token))
            now = str((v.json() or {}).get("status") or "").upper() if v.status_code == 200 else ""
            if now == want:
                return {"ok": True, "status": now.lower(), "detail": f"Campaign is now {now.lower()} (Marketing API)"}
            return {"ok": False, "status": now.lower() or "unknown", "detail": f"Write accepted but status reads '{now or 'unknown'}'"}
    except Exception as exc:  # noqa: BLE001 — any API-path failure falls back to sidecar
        logger.warning("LinkedIn ads control API path failed: %s", exc)
        return None


def _cm_campaigns_url() -> str:
    settings = get_settings()
    account_id = settings.LINKEDIN_AD_ACCOUNT_ID or "512642510"
    return f"{_CM_BASE}/accounts/{account_id}/campaigns"


async def _navigate(url: str) -> None:
    settings = get_settings()
    base = settings.LINKEDIN_BROWSER_SIDECAR_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=120.0) as client:
        r = await client.post(f"{base}/debug/navigate", json={"url": url})
        r.raise_for_status()
        await asyncio.sleep(10)


async def _eval(script: str) -> Any:
    settings = get_settings()
    base = settings.LINKEDIN_BROWSER_SIDECAR_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.post(f"{base}/debug/eval", json={"script": script})
        r.raise_for_status()
        data = r.json() or {}
        return data.get("result", data)


async def _page_text() -> str:
    settings = get_settings()
    base = settings.LINKEDIN_BROWSER_SIDECAR_URL.rstrip("/")
    async with httpx.AsyncClient(timeout=60.0) as client:
        r = await client.get(f"{base}/debug/page-text")
        r.raise_for_status()
        return (r.json() or {}).get("text", "")


def _campaign_status(text: str, campaign_id: str) -> str:
    """Extract the tracked ad set's status from the campaigns list text."""
    m = re.search(rf"{re.escape(campaign_id)}[^\n]*\n\n(\w+)", text)
    return m.group(1).strip().lower() if m else "unknown"


_SELECT_ROW_JS = """
try {
  const id = "%s";
  const rows = [...document.querySelectorAll("tr,[role=row],li")].filter(e => e.offsetParent && e.innerText.includes(id));
  if (!rows.length) { "row not found" }
  else {
    const row = rows[rows.length - 1];
    const cb = row.querySelector("input[type=checkbox],[role=checkbox]");
    if (cb) {
      const r = cb.getBoundingClientRect();
      ["pointerdown","mousedown","pointerup","mouseup","click"].forEach(t =>
        cb.dispatchEvent(new (t.startsWith("pointer") ? PointerEvent : MouseEvent)(t,
          {bubbles:true,cancelable:true,clientX:r.x+r.width/2,clientY:r.y+r.height/2})));
      "checkbox clicked"
    } else {
      const r = row.getBoundingClientRect();
      ["pointerdown","mousedown","pointerup","mouseup","click"].forEach(t =>
        row.dispatchEvent(new (t.startsWith("pointer") ? PointerEvent : MouseEvent)(t,
          {bubbles:true,cancelable:true,clientX:r.x+20,clientY:r.y+r.height/2})));
      "row clicked"
    }
  }
} catch(e) { "ERR:" + e.message }
"""

_CLICK_TEXT_JS = """
try {
  const want = "%s";
  const el = [...document.querySelectorAll("button,[role=menuitem],[role=option],li")]
    .find(e => e.offsetParent && e.innerText.trim() === want)
      || [...document.querySelectorAll("span,p")].find(e =>
           e.offsetParent && e.children.length === 0 && e.innerText.trim() === want);
  if (!el) { "not found: " + want }
  else {
    const r = el.getBoundingClientRect();
    ["pointerdown","mousedown","pointerup","mouseup","click"].forEach(t =>
      el.dispatchEvent(new (t.startsWith("pointer") ? PointerEvent : MouseEvent)(t,
        {bubbles:true,cancelable:true,clientX:r.x+r.width/2,clientY:r.y+r.height/2})));
    "clicked " + want
  }
} catch(e) { "ERR:" + e.message }
"""


async def set_campaign_status(action: str, db=None) -> dict[str, Any]:
    """Pause or resume the tracked ad set. ``action`` is ``pause``/``resume``.

    Returns ``{"ok": bool, "status": str, "detail": str}``.
    """
    settings = get_settings()
    campaign_id = settings.LINKEDIN_ADS_CAMPAIGN_ID
    if not campaign_id:
        return {"ok": False, "status": "unknown", "detail": "LINKEDIN_ADS_CAMPAIGN_ID not configured"}
    if action not in ("pause", "resume"):
        return {"ok": False, "status": "unknown", "detail": f"unknown action {action!r}"}

    want = "paused" if action == "pause" else "active"

    # Marketing API first — deterministic, no browser session needed.
    api_result = await _api_set_status(action, campaign_id, settings.LINKEDIN_AD_ACCOUNT_ID or "512642510", db=db)
    if api_result is not None:
        return api_result

    menu_item = "Pause" if action == "pause" else "Activate"

    try:
        await _navigate(_cm_campaigns_url())
        text = await _page_text()
        current = _campaign_status(text, campaign_id)
        if current == want:
            return {"ok": True, "status": current, "detail": f"Already {want}"}
        if current in ("unknown", ""):
            return {"ok": False, "status": "unknown", "detail": "Could not read campaign status (sidecar session may be logged out)"}

        # Select the ad set row, open "Set status", pick Pause/Activate.
        step = await _eval(_SELECT_ROW_JS % campaign_id)
        if "not found" in str(step) or "ERR" in str(step):
            return {"ok": False, "status": current, "detail": f"row select failed: {step}"}
        await asyncio.sleep(2)

        step = await _eval(_CLICK_TEXT_JS % "Set status")
        await asyncio.sleep(2)
        step = await _eval(_CLICK_TEXT_JS % menu_item)
        if "not found" in str(step) or "ERR" in str(step):
            return {"ok": False, "status": current, "detail": f"menu '{menu_item}' failed: {step}"}
        await asyncio.sleep(3)

        # Some flows show a confirm dialog — accept it if present.
        await _eval(_CLICK_TEXT_JS % "Confirm")
        await asyncio.sleep(3)

        # Verify.
        await _navigate(_cm_campaigns_url())
        text = await _page_text()
        now = _campaign_status(text, campaign_id)
        if now == want:
            return {"ok": True, "status": now, "detail": f"Campaign is now {now}"}
        return {"ok": False, "status": now, "detail": f"Toggled but status reads '{now}' — check Campaign Manager"}
    except httpx.HTTPError as exc:
        logger.warning("LinkedIn ads control failed: %s", exc)
        return {"ok": False, "status": "unknown", "detail": f"Sidecar error: {exc}"}


async def notify_slack(text: str, response_url: str | None = None) -> None:
    """Post the outcome back to Slack — response_url first, ads webhook fallback."""
    settings = get_settings()
    if response_url:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(
                    response_url,
                    json={
                        "response_type": "in_channel",
                        "text": text,
                    },
                )
                if r.status_code < 300:
                    return
        except httpx.HTTPError:
            pass
    await _post_slack_text(
        text=text,
        webhook_url=settings.SLACK_ADS_WEBHOOK_URL or settings.SLACK_WEBHOOK_URL,
        token="",
        channel_id=settings.SLACK_ADS_CHANNEL_ID or "",
        purpose="ads-control",
    )

"""Nextcloud Talk transport for SocialAuto notifications.

Mirrors the Slack notification surface onto Talk rooms on the omv Nextcloud
(cloud.cloudless.gr) so digests/alerts keep flowing when Slack is dead and
Slack can be retired later without touching the 36 call sites.

- Outbound posts: OCS chat API
  ``POST {base}/ocs/v2.php/apps/spreed/api/v1/chat/{room}`` with basic auth
  (app password). ``talk_send_message`` posts as the configured user.
- Bot replies (two-way commands): ``POST .../bot/{room}/message`` signed with
  ``NEXTCLOUD_TALK_BOT_SECRET`` — same HMAC-SHA256 scheme Talk uses to sign
  webhook deliveries to us (see app/api/talk_bot.py).

Config (all optional — nothing crashes without it):
    NEXTCLOUD_TALK_NOTIFY        master switch (default off)
    NEXTCLOUD_TALK_ROOM          default room token ("SocialAuto Ops")
    NEXTCLOUD_TALK_ROOM_ALERTS   alerts/approvals room token
    NEXTCLOUD_BASE_URL           https://cloud.cloudless.gr (derived from
                                 NEXTCLOUD_DAV_URL when unset)
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_OCS = "/ocs/v2.php/apps/spreed/api/v1"


def talk_enabled() -> bool:
    s = get_settings()
    return bool(
        getattr(s, "NEXTCLOUD_TALK_NOTIFY", False)
        and getattr(s, "NEXTCLOUD_USERNAME", "")
        and getattr(s, "NEXTCLOUD_APP_PASSWORD", "")
    )


def _base_url() -> str:
    s = get_settings()
    base = (getattr(s, "NEXTCLOUD_BASE_URL", "") or "").strip().rstrip("/")
    if base:
        return base
    dav = (getattr(s, "NEXTCLOUD_DAV_URL", "") or "").strip()
    m = re.match(r"(https?://[^/]+)", dav)
    return m.group(1) if m else ""


def room_token(purpose: str) -> str:
    """purpose → room token. alerts/approvals go to the alerts room when set,
    everything else to the default ops room."""
    s = get_settings()
    if purpose in ("alerts", "approvals"):
        return (getattr(s, "NEXTCLOUD_TALK_ROOM_ALERTS", "") or "").strip() or (
            getattr(s, "NEXTCLOUD_TALK_ROOM", "") or ""
        ).strip()
    return (getattr(s, "NEXTCLOUD_TALK_ROOM", "") or "").strip()


def blocks_to_text(blocks: list[dict] | None) -> str:
    """Flatten Slack Block Kit to plain text for Talk — buttons become
    '• Label → url' lines (action-only buttons keep their action_id hint)."""
    if not blocks:
        return ""
    out: list[str] = []
    for block in blocks:
        if block.get("type") == "section":
            t = (block.get("text") or {}).get("text")
            if t:
                out.append(str(t))
        elif block.get("type") == "actions":
            for el in block.get("elements") or []:
                label = ((el.get("text") or {}).get("text") or "").strip()
                url = el.get("url") or ""
                if label and url:
                    out.append(f"• {label} → {url}")
                elif label:
                    out.append(f"• {label} ({el.get('action_id', '')})")
        elif block.get("type") == "context":
            parts = [
                (e.get("text") or "")
                for e in block.get("elements") or []
                if isinstance(e, dict)
            ]
            if parts:
                out.append(" · ".join(p for p in parts if p))
    return "\n".join(out)


async def post_to_talk(text: str, *, purpose: str = "digest",
                       extra: str = "") -> bool:
    """Post a message to the mapped Talk room. Best-effort — never raises."""
    if not talk_enabled():
        return False
    token = room_token(purpose)
    base = _base_url()
    if not token or not base:
        logger.debug("Talk notify: no room/base configured for %s", purpose)
        return False
    s = get_settings()
    body = text if not extra else f"{text}\n\n{extra}"
    body = body[:30000]  # Talk chat max-length is 32k; stay under
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base}{_OCS}/chat/{token}",
                json={"message": body},
                auth=(s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD),
                headers={"OCS-APIRequest": "true"},
            )
            if resp.status_code >= 300:
                logger.warning(
                    "Talk %s post failed: HTTP %s %s", purpose,
                    resp.status_code, resp.text[:150],
                )
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Talk %s post failed: %s", purpose, exc)
        return False


# ── bot (two-way) ────────────────────────────────────────────────────────────


def verify_bot_request(random: str, body: bytes, signature: str) -> bool:
    """HMAC-SHA256(random + body, secret) per nextcloud/spreed docs/bots.md."""
    secret = (get_settings().NEXTCLOUD_TALK_BOT_SECRET or "").strip()
    if not secret or not random or not signature:
        return False
    digest = hmac.new(
        secret.encode(), random.encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, signature.lower())


async def bot_reply(room: str, message: str, *, reply_to: int | None = None) -> bool:
    """Reply into a room as the installed Talk bot.

    URL parts are config-derived only — the room must be one of the
    configured rooms and the base always comes from settings, so nothing
    requester-controlled reaches the request (CodeQL partial-SSRF).
    """
    secret = (get_settings().NEXTCLOUD_TALK_BOT_SECRET or "").strip()
    base = _base_url()
    allowed = {room_token("digest"), room_token("alerts")} - {""}
    if not secret or not base or room not in allowed:
        return False
    rnd = hashlib.sha256(os.urandom(32)).hexdigest()
    # Outbound signature = HMAC(random + message string) per spreed docs.
    sig = hmac.new(
        secret.encode(), rnd.encode() + message.encode(), hashlib.sha256
    ).hexdigest()
    payload: dict = {
        "message": message[:30000],
        "referenceId": hashlib.sha256(rnd.encode()).hexdigest(),
    }
    if reply_to:
        payload["replyTo"] = reply_to
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base}{_OCS}/bot/{room}/message",
                json=payload,
                headers={
                    "OCS-APIRequest": "true",
                    "Accept": "application/json",
                    "X-Nextcloud-Talk-Bot-Random": rnd,
                    "X-Nextcloud-Talk-Bot-Signature": sig,
                },
            )
            if resp.status_code == 400 and reply_to:
                # replyTo invalid (system message, deleted, etc.) — send without it
                payload.pop("replyTo", None)
                resp = await client.post(
                    f"{base}{_OCS}/bot/{room}/message",
                    json=payload,
                    headers={
                        "OCS-APIRequest": "true",
                        "Accept": "application/json",
                        "X-Nextcloud-Talk-Bot-Random": rnd,
                        "X-Nextcloud-Talk-Bot-Signature": sig,
                    },
                )
            if resp.status_code not in (200, 201):
                logger.warning(
                    "Talk bot reply failed: HTTP %s %s",
                    resp.status_code, resp.text[:150],
                )
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Talk bot reply failed: %s", exc)
        return False

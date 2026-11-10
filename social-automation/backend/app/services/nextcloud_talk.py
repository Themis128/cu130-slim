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
_OCS_V4 = "/ocs/v2.php/apps/spreed/api/v4"
_ROOM_TOKEN_RE = re.compile(r"^[a-z0-9]{8}$")


def valid_room_token(token: str) -> bool:
    """Talk room tokens are 8-char lowercase alnum — anything else never
    reaches a request URL (SSRF guard for API-derived tokens)."""
    return bool(_ROOM_TOKEN_RE.match(token or ""))


def talk_enabled() -> bool:
    s = get_settings()
    return bool(getattr(s, "NEXTCLOUD_TALK_NOTIFY", False) and getattr(s, "NEXTCLOUD_USERNAME", "") and getattr(s, "NEXTCLOUD_APP_PASSWORD", ""))


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
    changelog to the changelog room when set, everything else to the
    default ops room."""
    s = get_settings()
    if purpose in ("alerts", "approvals"):
        return (getattr(s, "NEXTCLOUD_TALK_ROOM_ALERTS", "") or "").strip() or (getattr(s, "NEXTCLOUD_TALK_ROOM", "") or "").strip()
    if purpose == "changelog":
        return (getattr(s, "NEXTCLOUD_TALK_ROOM_CHANGELOG", "") or "").strip() or (getattr(s, "NEXTCLOUD_TALK_ROOM", "") or "").strip()
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
            parts = [(e.get("text") or "") for e in block.get("elements") or [] if isinstance(e, dict)]
            if parts:
                out.append(" · ".join(p for p in parts if p))
    return "\n".join(out)


async def post_to_talk(text: str, *, purpose: str = "digest", extra: str = "") -> bool:
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
                    "Talk %s post failed: HTTP %s %s",
                    purpose,
                    resp.status_code,
                    resp.text[:150],
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
    digest = hmac.new(secret.encode(), random.encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, signature.lower())


async def bot_reply(room: str, message: str, *, reply_to: int | None = None) -> bool:
    """Reply into a room as the installed Talk bot.

    URL parts are config-derived only — the room must be one of the
    configured rooms and the base always comes from settings, so nothing
    requester-controlled reaches the request (CodeQL partial-SSRF).
    """
    secret = (get_settings().NEXTCLOUD_TALK_BOT_SECRET or "").strip()
    base = _base_url()
    # Resolve against configured rooms and substitute the *config* value —
    # the requester's string never reaches the URL (CodeQL partial-SSRF).
    configured = {room_token("digest"), room_token("alerts")} - {""}
    room = next((c for c in configured if c == room), "")
    if not secret or not base or not room:
        return False
    rnd = hashlib.sha256(os.urandom(32)).hexdigest()
    # Outbound signature = HMAC(random + message string) per spreed docs.
    sig = hmac.new(secret.encode(), rnd.encode() + message.encode(), hashlib.sha256).hexdigest()
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
                    resp.status_code,
                    resp.text[:150],
                )
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Talk bot reply failed: %s", exc)
        return False


# ── room management (consultation rooms, meeting notes) ──────────────────────
#
# Public rooms (type 3) are joinable by guests via /call/{token} — the same
# pattern as the existing "Cloudless Consultations" room. `roomName` (not
# `name`) is the create/update field; verified live against spreed 25.x.


async def create_room(name: str, *, description: str = "", room_type: int = 3) -> str | None:
    """Create a Talk room; returns the room token or None. Never raises."""
    if not talk_enabled():
        return None
    base = _base_url()
    if not base:
        return None
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base}{_OCS_V4}/room",
                data={
                    "roomType": str(room_type),
                    "roomName": name[:255],
                    "description": description[:4000],
                },
                auth=(s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD),
                headers={"OCS-APIRequest": "true"},
            )
            if resp.status_code not in (200, 201):
                logger.warning("Talk room create failed: HTTP %s %s", resp.status_code, resp.text[:150])
                return None
            token = (resp.json().get("ocs", {}).get("data") or {}).get("token")
            return token if valid_room_token(token or "") else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("Talk room create failed: %s", exc)
        return None


async def delete_room(token: str) -> bool:
    """Delete a room we own. Best-effort; never raises."""
    if not talk_enabled() or not valid_room_token(token):
        return False
    base = _base_url()
    if not base:
        return False
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.delete(
                f"{base}{_OCS_V4}/room/{token}",
                auth=(s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD),
                headers={"OCS-APIRequest": "true"},
            )
            return resp.status_code in (200, 204)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Talk room delete failed: %s", exc)
        return False


async def post_to_room(token: str, text: str) -> bool:
    """Post into an arbitrary (validated) room — unlike post_to_talk this is
    for caller-supplied tokens such as per-consultation rooms."""
    if not talk_enabled() or not valid_room_token(token):
        return False
    base = _base_url()
    if not base:
        return False
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base}{_OCS}/chat/{token}",
                json={"message": text[:30000]},
                auth=(s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD),
                headers={"OCS-APIRequest": "true"},
            )
            if resp.status_code >= 300:
                logger.warning("Talk room post failed: HTTP %s %s", resp.status_code, resp.text[:150])
                return False
            return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Talk room post failed: %s", exc)
        return False


async def list_room_messages(token: str, *, limit: int = 200, last_known_id: int = 0) -> list[dict]:
    """Recent chat messages for a room (oldest→newest as returned)."""
    if not talk_enabled() or not valid_room_token(token):
        return []
    base = _base_url()
    if not base:
        return []
    s = get_settings()
    params = {"lookIntoFuture": "0", "limit": str(min(limit, 500)), "format": "json"}
    if last_known_id:
        params["lastKnownMessageId"] = str(last_known_id)
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{base}{_OCS}/chat/{token}",
                params=params,
                auth=(s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD),
                headers={"OCS-APIRequest": "true"},
            )
            if resp.status_code != 200:
                logger.warning("Talk chat list failed: HTTP %s %s", resp.status_code, resp.text[:150])
                return []
            data = resp.json().get("ocs", {}).get("data")
            return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        logger.warning("Talk chat list failed: %s", exc)
        return []


async def ping() -> bool:
    """Liveness probe — capabilities endpoint proves auth + reachability.
    Distinguishes API-down from an empty room (unlike list_room_messages)."""
    if not talk_enabled():
        return False
    base = _base_url()
    if not base:
        return False
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                f"{base}/ocs/v2.php/cloud/capabilities",
                params={"format": "json"},
                auth=(s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD),
                headers={"OCS-APIRequest": "true"},
            )
            return resp.status_code == 200
    except Exception:  # noqa: BLE001
        return False


_VIDEO_EXT = (".mp4", ".webm", ".mkv", ".mov", ".m4v")


def find_recording_file(messages: list[dict]) -> dict | None:
    """Pick the newest shared recording file from chat messages.

    A finished Talk recording is shared into the room as a regular file
    message whose ``messageParameters.file`` carries ``id`` + ``name`` —
    we take the last video-looking one (recordings land after the call).
    """
    found: dict | None = None
    for msg in messages:
        params = msg.get("messageParameters") or {}
        fobj = params.get("file") or {}
        if not isinstance(fobj, dict):
            continue
        fid = fobj.get("id")
        name = str(fobj.get("name") or fobj.get("fileName") or "").lower()
        if not fid:
            continue
        if name.endswith(_VIDEO_EXT) or "recording" in name or "recording" in str(params.get("type") or ""):
            found = {"file_id": int(fid), "name": name, "message_id": msg.get("id")}
    return found


async def download_talk_file(file_id: int) -> tuple[bytes, str] | None:
    """Download a Talk-shared file via ``/index.php/f/{id}`` (auth → DAV)."""
    if not talk_enabled() or not isinstance(file_id, int) or file_id <= 0:
        return None
    base = _base_url()
    if not base:
        return None
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
            resp = await client.get(
                f"{base}/index.php/f/{file_id}",
                auth=(s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD),
            )
            if resp.status_code != 200 or not resp.content:
                logger.warning("Talk file download failed: HTTP %s", resp.status_code)
                return None
            ctype = resp.headers.get("content-type", "").split(";")[0] or "application/octet-stream"
            return resp.content, ctype
    except Exception as exc:  # noqa: BLE001
        logger.warning("Talk file download failed: %s", exc)
        return None


def room_url(token: str) -> str:
    return f"{_base_url()}/call/{token}" if _base_url() else ""

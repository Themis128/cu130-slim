"""Nextcloud Talk ↔ business messenger bridge.

Mirrors external DM threads into per-contact Nextcloud Talk rooms:

- Inbound: a Celery beat (`talk_bridge_sync`) polls each platform's
  conversation APIs and posts new messages into the mapped Talk room
  via the Talk bot (``bot_reply`` → falls back to the app-password
  chat API when no bot is provisioned).
- Outbound: the existing Talk bot webhook (``app.api.talk_bot``)
  delivers user messages posted in a mapped room; ``outbound`` routes
  the text through the platform's send lane (Messenger Graph API,
  personal-Messenger browser bridge, Instagram Messaging API,
  LinkedIn conversation sidecar).

Room naming is deliberately chat-like — ``FB · Jane Doe``,
``IG · @user``, ``LI · Jane Doe`` — so the stock Talk Android app and
the Windows Talk desktop client read like any other chat client.
"""

from __future__ import annotations

import logging
import os
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.db.session import async_session_maker
from app.models.social_account import SocialAccount
from app.models.talk_room import TalkRoom
from app.services.nextcloud_talk import bot_reply, create_room, post_to_room

logger = logging.getLogger(__name__)

PLATFORM_PREFIX = {
    "messenger": "FB",
    "personal_messenger": "FB",
    "instagram": "IG",
    "linkedin": "LI",
    "whatsapp": "WA",
}

MAX_SYNC_ROOMS = 40
INBOUND_LIMIT = 20


def bridge_enabled() -> bool:
    s = get_settings()
    secret = getattr(s, "NEXTCLOUD_TALK_BOT_SECRET", "") or os.getenv("NEXTCLOUD_TALK_BOT_SECRET", "")
    return bool(
        getattr(s, "TALK_BRIDGE_ENABLED", False)
        and getattr(s, "NEXTCLOUD_TALK_NOTIFY", False)
        and secret
    )


def _room_name(platform: str, contact: str) -> str:
    prefix = PLATFORM_PREFIX.get(platform, platform.upper()[:4])
    return f"{prefix} · {contact or 'Unknown'}"[:100]


def _format_inbound(sender: str, text: str, ours: bool, attachments: list[str]) -> str:
    label = "You" if ours else (sender or "Them")
    body = text or ""
    for a in attachments:
        body += f"\n📎 {a}"
    if not body.strip():
        body = "(non-text message)"
    return f"**{label}**\n{body}"


async def _room_for(db, team_id: uuid.UUID, platform: str, thread_id: str) -> TalkRoom | None:
    res = await db.execute(
        select(TalkRoom).where(
            TalkRoom.team_id == team_id,
            TalkRoom.platform == platform,
            TalkRoom.thread_id == thread_id,
        )
    )
    return res.scalar_one_or_none()


async def room_for_token(room_token: str) -> TalkRoom | None:
    """Look up a mapped room by Talk room token (bot-webhook inbound routing)."""
    async with async_session_maker() as db:
        res = await db.execute(select(TalkRoom).where(TalkRoom.room_token == room_token))
        return res.scalar_one_or_none()


async def _log_event(db, room: TalkRoom, direction: str, msg: dict | None = None, text: str = "") -> None:
    """Record a bridged message as an analytics event → datalake export.

    Every inbound mirror + outbound send lands in analytics_events so the
    export-datalake sweep carries the messaging activity for analytics.
    Savepointed so an FK/type failure never poisons the caller's transaction.
    """
    from datetime import UTC, datetime

    from app.models.analytics import AnalyticsEvent

    try:
        account_uuid = uuid.UUID(room.account_id)
    except (ValueError, TypeError):
        account_uuid = None
    body = (msg or {}).get("text") or text
    try:
        async with db.begin_nested():
            db.add(
                AnalyticsEvent(
                    team_id=room.team_id,
                    social_account_id=account_uuid,
                    event_type=f"talk_bridge_{direction}",
                    platform=room.platform,
                    platform_event_id=(msg or {}).get("id") or None,
                    occurred_at=datetime.now(UTC),
                    meta_data={
                        "room_token": room.room_token,
                        "thread_id": room.thread_id,
                        "contact_name": room.contact_name,
                        "sender": (msg or {}).get("sender", ""),
                        "text_preview": (body or "")[:500],
                        "attachments": (msg or {}).get("attachments", []),
                    },
                )
            )
    except Exception:
        logger.exception("talk-bridge analytics log failed")


async def _post(room: TalkRoom, text: str, db=None) -> bool:
    """Post into the room as the Talk bot (needs `occ talk:bot:setup <bot> <room>`
    per room), falling back to the app-password user. Returns True when a
    message actually reached the room."""
    if await bot_reply(room.room_token, text, db=db):
        return True
    return await post_to_room(room.room_token, text)


# ─── inbound message fetchers (normalized dicts) ────────────────────


def _attachment_urls(attachments: object) -> list[str]:
    if not isinstance(attachments, dict):
        return []
    out = []
    for a in attachments.get("data", []):
        if isinstance(a, dict):
            out.append((a.get("image_data") or {}).get("url") or a.get("file_url") or "attachment")
    return out


def _norm(ts_key: str, msg_id: str, sender: str, text: str, ours: bool, atts: list[str] | None = None) -> dict:
    # Lanes without message ids/timestamps (personal Messenger scrape,
    # LinkedIn) still need a stable key or the watermark can never advance —
    # synthesize a deterministic one from the content itself.
    key = ts_key or msg_id
    if not key:
        import hashlib

        key = "h_" + hashlib.sha256(f"{sender}|{text}|{ours}".encode()).hexdigest()[:16]
    return {"key": key, "id": msg_id, "sender": sender, "text": text, "ours": ours, "attachments": atts or []}


def _keyless_newest_first(raw: list, to_msg) -> list[dict]:
    """Normalize a chronological (oldest-first) scrape/feed to the bridge's
    newest-first contract. Repeated identical texts get an occurrence suffix
    counted from the oldest end, so a fresh repeat of the same DM does not
    collide with the watermark of an earlier identical one."""
    seen: dict[str, int] = {}
    out: list[dict] = []
    for m in raw:  # walk oldest→newest, suffix duplicates stably
        msg = to_msg(m)
        if msg is not None:
            seen[msg["key"]] = seen.get(msg["key"], 0) + 1
            if seen[msg["key"]] > 1:
                msg["key"] = f"{msg['key']}#{seen[msg['key']]}"
            out.append(msg)
    out.reverse()  # newest first
    return out


async def _fb_page_messages(account: SocialAccount, thread_id: str) -> list[dict]:
    from app.core.security import decrypt_token
    from app.services.messenger_api import MessengerAPIClient

    meta = account.meta_data or {}
    token = meta.get("page_token") or ""
    if token and not str(token).startswith("EA"):
        try:
            token = decrypt_token(token.encode() if isinstance(token, str) else token)
        except Exception:
            pass
    client = MessengerAPIClient(access_token=token, page_id=account.account_id)
    raw = await client.get_conversation_messages(thread_id, limit=INBOUND_LIMIT)
    out = []
    for m in raw:
        frm = m.get("from") or {}
        atts = _attachment_urls(m.get("attachments"))
        ours = frm.get("id") == account.account_id
        out.append(_norm(m.get("created_time", ""), m.get("id", ""), frm.get("name", ""), m.get("message", ""), ours, atts))
    return out


async def _fb_personal_messages(account: SocialAccount, thread_id: str, is_e2ee: bool) -> list[dict]:
    from app.services.browser_bridge import BrowserBridgeClient

    bridge = BrowserBridgeClient(
        os.getenv("BROWSER_BRIDGE_URL", "http://browser-novnc:9223"), platform="facebook"
    )
    session = await bridge.ensure_session("facebook")
    if session.get("status") != "active":
        return []
    data = await bridge.get_personal_messenger_messages(thread_id, is_e2ee=is_e2ee)
    if isinstance(data, dict):
        seen_thread = str(data.get("thread_id") or data.get("conversation_id") or "")
        if seen_thread and seen_thread != str(thread_id):
            logger.warning("talk-bridge: browser returned thread %s, wanted %s — dropped", seen_thread, thread_id)
            return []
    raw = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []

    def to_msg(m):
        if not isinstance(m, dict):
            return None
        return _norm(
            str(m.get("ts") or m.get("created_time") or m.get("id") or ""),
            str(m.get("id") or ""),
            m.get("sender") or m.get("author") or "",
            m.get("text") or m.get("body") or "",
            bool(m.get("is_own") or m.get("ours")),
            m.get("attachments") or [],
        )

    # Browser scrape returns DOM order (oldest first) — flip to newest-first.
    return _keyless_newest_first(raw, to_msg)


def _ig_client(account: SocialAccount):
    """InstagramAPIClient honoring both token stores + the Business Login host.

    Business Login accounts (instagram2_callback) keep the token in
    access_token_enc and must call graph.instagram.com; Page-linked accounts
    keep it in meta_data and use graph.facebook.com.
    """
    from app.core.security import decrypt_token
    from app.services.instagram_api import InstagramAPIClient

    meta = account.meta_data or {}
    token = ""
    if account.access_token_enc:
        try:
            enc = account.access_token_enc
            token = decrypt_token(enc.encode() if isinstance(enc, str) else enc)
        except Exception:
            token = ""
    if not token:
        token = meta.get("access_token") or meta.get("page_token") or ""
        if token and not str(token).startswith(("EA", "IGA")):
            try:
                token = decrypt_token(token.encode() if isinstance(token, str) else token)
            except Exception:
                pass
    ig_user_id = meta.get("ig_user_id") or meta.get("ig_business_id") or account.account_id
    return InstagramAPIClient(
        access_token=token,
        ig_user_id=ig_user_id,
        use_business_login_api=meta.get("login_type") == "business_login",
    )


async def _ig_messages(account: SocialAccount, thread_id: str) -> list[dict]:
    meta = account.meta_data or {}
    ig_user_id = meta.get("ig_user_id") or meta.get("ig_business_id") or account.account_id
    client = _ig_client(account)
    data = await client.get_dm_messages(thread_id, limit=INBOUND_LIMIT)
    raw = data.get("data") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    out = []
    for m in raw:
        frm = m.get("from") or {}
        fid = frm.get("id") or ""
        atts = _attachment_urls(m.get("attachments"))
        out.append(
            _norm(
                str(m.get("created_time") or ""),
                str(m.get("id") or ""),
                frm.get("username") or frm.get("name") or "",
                m.get("message") or "",
                fid == str(ig_user_id),
                atts,
            )
        )
    return out


_LI_SELF_CACHE_TTL = 900  # profile scrape is expensive; own name rarely changes
_li_self: dict = {"name": None, "at": 0.0}


async def _li_self_name(sidecar) -> str:
    """Own LinkedIn display name (cached) — the only reliable `ours` marker
    the message scrape can be compared against; sender-name-vs-contact
    heuristics mislabel other participants in group chats."""
    import time

    if _li_self["name"] is not None and time.time() - _li_self["at"] < _LI_SELF_CACHE_TTL:
        return _li_self["name"]
    try:
        prof = await sidecar.get_profile()
        _li_self["name"] = ((prof or {}).get("name") or "").strip()
    except Exception:
        _li_self["name"] = ""
    _li_self["at"] = time.time()
    return _li_self["name"]


async def _li_messages(account: SocialAccount, thread_id: str) -> list[dict]:
    from app.services.linkedin_sidecar import LinkedInSidecarClient

    sidecar = LinkedInSidecarClient()
    data = await sidecar.get_thread_messages(thread_id)
    raw = data.get("messages") if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    self_name = (await _li_self_name(sidecar)).lower()
    # The sidecar's own-profile scrape can land on a wrong /in/ link — only
    # trust it when it agrees with the stored account name; a mismatch means
    # NO messages get the "You" label rather than a wrong participant getting it.
    expected = (getattr(account, "display_name", None) or getattr(account, "username", None) or "").lower()
    if self_name and expected:
        # Exact normalized match only — a containment check would trust
        # "Alex Chen Jr." for owner "Alex Chen" and then label THAT group
        # member's messages "You". A non-match degrades to no "You" labels.
        if self_name != expected:
            logger.warning("talk-bridge: sidecar self name %r != account %r — attribution off", self_name, expected)
            self_name = ""

    def to_msg(m):
        if not isinstance(m, dict):
            return None
        sender = m.get("sender") or m.get("author") or ""
        ours = m.get("is_own") if "is_own" in m else m.get("ours")
        if ours is None:
            ours = bool(sender) and bool(self_name) and sender.strip().lower() == self_name
        return _norm(
            str(m.get("ts") or m.get("created_at") or m.get("id") or ""),
            str(m.get("id") or ""),
            sender,
            m.get("text") or m.get("body") or "",
            bool(ours),
            m.get("attachments") or [],
        )

    # Sidecar feed is chronological (oldest first) — flip to newest-first.
    return _keyless_newest_first(raw, to_msg)


async def _fetch_messages(account: SocialAccount | None, room: TalkRoom) -> list[dict]:
    if account is None:
        return []
    try:
        if room.platform == "messenger":
            return await _fb_page_messages(account, room.thread_id)
        if room.platform == "personal_messenger":
            return await _fb_personal_messages(account, room.thread_id, room.is_e2ee)
        if room.platform == "instagram":
            return await _ig_messages(account, room.thread_id)
        if room.platform == "linkedin":
            return await _li_messages(account, room.thread_id)
    except Exception:
        logger.exception("talk-bridge inbound fetch failed: %s %s", room.platform, room.thread_id)
    return []


# ─── room sync ───────────────────────────────────────────────────────


async def _conversation_list(account: SocialAccount) -> list[dict]:
    """Normalized conversation rows for one account.

    Each row: {thread_id, contact_name, recipient_id, is_e2ee} — the
    recipient the outbound lane needs (psid / ig-user-id / li thread).
    """
    platform = (account.platform or "").lower()
    account_type = (account.account_type or "").lower()
    try:
        if platform == "facebook" and account_type == "page":
            from app.core.security import decrypt_token
            from app.services.messenger_api import MessengerAPIClient

            meta = account.meta_data or {}
            token = meta.get("page_token") or ""
            if token and not str(token).startswith("EA"):
                try:
                    token = decrypt_token(token.encode() if isinstance(token, str) else token)
                except Exception:
                    pass
            client = MessengerAPIClient(access_token=token, page_id=account.account_id)
            convs = await client.get_conversations(platform="messenger", limit=MAX_SYNC_ROOMS)
            out = []
            for c in convs:
                parts = c.get("participants") or {}
                plist = parts.get("data") if isinstance(parts, dict) else parts or []
                other = next((p for p in plist if str(p.get("id")) != str(account.account_id)), {})
                out.append({
                    "thread_id": c.get("id", ""),
                    "contact_name": other.get("name", ""),
                    "recipient_id": other.get("id", ""),
                    "is_e2ee": False,
                    "platform": "messenger",
                })
            return out
        if platform == "facebook" and account_type == "user":
            from app.services.browser_bridge import BrowserBridgeClient

            bridge = BrowserBridgeClient(
                os.getenv("BROWSER_BRIDGE_URL", "http://browser-novnc:9223"), platform="facebook"
            )
            data = await bridge.get_personal_messenger_conversations()
            raw = data.get("conversations") if isinstance(data, dict) else data
            if not isinstance(raw, list):
                return []
            return [{
                "thread_id": str(c.get("thread_id") or c.get("id") or ""),
                "contact_name": c.get("name") or c.get("sender") or "",
                "recipient_id": str(c.get("thread_id") or c.get("id") or ""),
                "is_e2ee": bool(c.get("e2ee") or c.get("is_e2ee")),
                "platform": "personal_messenger",
            } for c in raw if isinstance(c, dict)]
        if platform == "instagram":
            meta = account.meta_data or {}
            ig_user_id = meta.get("ig_user_id") or meta.get("ig_business_id") or account.account_id
            client = _ig_client(account)
            data = await client.get_conversations(limit=MAX_SYNC_ROOMS)
            raw = data.get("data") if isinstance(data, dict) else data
            if not isinstance(raw, list):
                return []
            out = []
            for c in raw:
                parts = (c.get("participants") or {}).get("data") or []
                other = next((p for p in parts if str(p.get("id")) != str(ig_user_id)), {})
                out.append({
                    "thread_id": c.get("id", ""),
                    "contact_name": other.get("username") or other.get("name", ""),
                    "recipient_id": str(other.get("id", "")),
                    "is_e2ee": False,
                    "platform": "instagram",
                })
            return out
        if platform == "linkedin":
            import re

            from app.services.linkedin_sidecar import LinkedInSidecarClient

            data = await LinkedInSidecarClient().get_conversations()
            raw = data.get("conversations") if isinstance(data, dict) else data
            if not isinstance(raw, list):
                return []

            def _tid(c):
                # sidecar falls back to the raw thread URL when the id regex
                # misses — route segments need the bare id, not a path.
                raw_id = str(c.get("id") or c.get("thread_id") or "")
                m = re.search(r"thread:(\d+)", raw_id)
                if m:
                    return m.group(1)
                return raw_id.rstrip("/").split("/")[-1] or raw_id

            return [{
                "thread_id": _tid(c),
                "contact_name": c.get("name") or c.get("participant") or "",
                "recipient_id": _tid(c),
                "is_e2ee": False,
                "platform": "linkedin",
            } for c in raw if isinstance(c, dict)]
    except Exception:
        logger.exception("talk-bridge conversation list failed: %s %s", platform, account.id)
    return []


async def sync_rooms(team_id: uuid.UUID, session_factory=async_session_maker) -> dict[str, int]:
    """Ensure one Talk room per active platform conversation."""
    created = mapped = 0
    async with session_factory() as db:
        res = await db.execute(
            select(SocialAccount).where(
                SocialAccount.team_id == team_id,
                SocialAccount.status == "active",
            )
        )
        accounts = res.scalars().all()

        for account in accounts:
            for conv in (await _conversation_list(account))[:MAX_SYNC_ROOMS]:
                if not conv["thread_id"]:
                    continue
                room = await _room_for(db, team_id, conv["platform"], conv["thread_id"])
                if room is None:
                    token = await create_room(_room_name(conv["platform"], conv["contact_name"]), room_type=2)
                    if not token:
                        continue
                    room = TalkRoom(
                        team_id=team_id,
                        platform=conv["platform"],
                        account_id=str(account.id),
                        thread_id=conv["thread_id"],
                        contact_name=conv["contact_name"],
                        recipient_id=conv["recipient_id"],
                        is_e2ee=conv["is_e2ee"],
                        room_token=token,
                        enabled=True,
                    )
                    db.add(room)
                    await db.flush()
                    created += 1
                    await _post(
                        room,
                        f"💬 {conv['platform'].replace('_', ' ').title()} thread with "
                        f"{conv['contact_name'] or 'contact'} — replies in this room are "
                        f"sent to the conversation. Type `mute` to pause.",
                    )
                else:
                    if str(room.account_id) != str(account.id):
                        room.account_id = str(account.id)  # rebind after account reconnect
                    if conv["contact_name"] and room.contact_name != conv["contact_name"]:
                        room.contact_name = conv["contact_name"]
                    if conv["recipient_id"] and not room.recipient_id:
                        room.recipient_id = conv["recipient_id"]
                    mapped += 1
        await db.commit()
    return {"created": created, "mapped": mapped}


async def inbound_sync(team_id: uuid.UUID, session_factory=async_session_maker) -> dict[str, int]:
    """Post new platform messages into their mapped Talk rooms."""
    posted = 0
    async with session_factory() as db:
        res = await db.execute(
            select(TalkRoom).where(TalkRoom.team_id == team_id, TalkRoom.enabled.is_(True))
        )
        rooms = res.scalars().all()
        acct_res = await db.execute(
            select(SocialAccount).where(SocialAccount.team_id == team_id)
        )
        accounts = {str(a.id): a for a in acct_res.scalars().all()}

        for room in rooms:
            msgs = await _fetch_messages(accounts.get(room.account_id), room)
            if not msgs:
                continue
            # messages arrive newest-first; post oldest-new of the unseen ones
            unseen: list[dict] = []
            for m in msgs:
                # empty last_inbound_key = new room, everything is unseen
                if room.last_inbound_key and (m["id"] or m["key"]) == room.last_inbound_key:
                    break
                unseen.append(m)
            if not unseen:
                continue
            last_posted_key: str | None = None
            for m in reversed(unseen[:10]):  # newest 10, chronological; older backlog stays unread
                ok = await _post(room, _format_inbound(m["sender"], m["text"], m["ours"], m["attachments"]), db=db)
                if not ok:
                    break  # leave the rest for the next sweep
                await _log_event(db, room, "outbound" if m["ours"] else "inbound", msg=m)
                posted += 1
                last_posted_key = m["id"] or m["key"] or None
            # Advance only to the last DELIVERED message — failed posts are
            # retried, delivered ones never repost.
            if last_posted_key:
                room.last_inbound_key = last_posted_key
                await db.commit()  # per-room: a crash must not replay delivered messages
    return {"posted": posted}


# ─── outbound (Talk → platform) ─────────────────────────────────────


async def outbound(room_token: str, text: str, session_factory=async_session_maker) -> tuple[bool, str]:
    """Route a Talk message to the mapped platform thread."""
    if not bridge_enabled():
        return False, "bridge disabled"
    room = await room_for_token(room_token)
    if room is None:
        return False, "not a bridged room"
    if not room.enabled:
        return False, "bridge muted for this room"

    async with session_factory() as db:
        res = await db.execute(
            select(SocialAccount).where(SocialAccount.id == uuid.UUID(room.account_id))
        )
        account = res.scalar_one_or_none()
    if account is None:
        return False, "account row missing"

    try:
        if room.platform == "messenger":
            from app.core.security import decrypt_token
            from app.services.messenger_api import MessengerAPIClient

            meta = account.meta_data or {}
            token = meta.get("page_token") or ""
            if token and not str(token).startswith("EA"):
                try:
                    token = decrypt_token(token.encode() if isinstance(token, str) else token)
                except Exception:
                    pass
            client = MessengerAPIClient(access_token=token, page_id=account.account_id)
            recipient = room.recipient_id or room.thread_id
            await client.send_text(recipient, text)
        elif room.platform == "personal_messenger":
            from app.services.browser_bridge import BrowserBridgeClient

            bridge = BrowserBridgeClient(
                os.getenv("BROWSER_BRIDGE_URL", "http://browser-novnc:9223"), platform="facebook"
            )
            session = await bridge.ensure_session("facebook")
            if session.get("status") != "active":
                return False, session.get("message", "bridge session inactive")
            res = await bridge.send_personal_messenger_message(room.thread_id, text, is_e2ee=room.is_e2ee)
            if not isinstance(res, dict) or res.get("status") != "sent":
                return False, "browser send failed — composer not found or send rejected"
        elif room.platform == "instagram":
            client = _ig_client(account)
            recipient = room.recipient_id
            if not recipient:
                return False, "no recipient id mapped — reply once in the native IG app first"
            await client.send_dm(recipient, text)
        elif room.platform == "linkedin":
            from app.services.linkedin_sidecar import LinkedInSidecarClient

            await LinkedInSidecarClient().send_message(room.thread_id, text)
        else:
            return False, f"platform {room.platform} not bridged for sending"
    except Exception as e:  # platform errors are the message the user needs
        logger.exception("talk-bridge outbound failed: %s %s", room.platform, room.thread_id)
        return False, str(e)[:300]

    try:
        async with async_session_maker() as db:
            await _log_event(db, room, "outbound", text=text)
            await db.commit()
    except Exception:
        logger.exception("talk-bridge analytics log failed")
    return True, "sent"

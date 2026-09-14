"""Telegram group watch — buffer messages, keyword/mention DM alerts, digests.

Based on official Bot API:
https://core.telegram.org/bots/api#getting-updates
https://core.telegram.org/bots/api#forwardmessage
https://core.telegram.org/bots/api#sendmessage

Requires BotFather Group Privacy OFF so the bot sees all group messages.
Owner must /start the bot in a private chat before digests/alerts can DM them.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.core.config import get_settings
from app.services.telegram_api import MAX_MESSAGE_CHARS, TelegramAPIClient, TelegramAPIError

logger = logging.getLogger(__name__)
settings = get_settings()

META_KEY = "telegram_group_watch"
_BUF_KEY = "telegram:group_buf:{account_id}:{chat_id}"
_ALERT_CD_KEY = "telegram:group_alert_cd:{account_id}:{chat_id}:{fingerprint}"
_DIGEST_SENT_KEY = "telegram:group_digest_sent:{account_id}:{day}"
_BUF_TTL_SECONDS = 48 * 3600
_ALERT_COOLDOWN_SECONDS = 120
_MAX_BUFFER = 200

DEFAULT_KEYWORDS = [
    "price",
    "pricing",
    "τιμή",
    "τιμες",
    "help",
    "urgent",
    "audit",
    "cloudless",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "enabled": False,
    "owner_chat_id": None,
    "owner_username": None,
    "watched_chats": [],
    "watch_all_groups": True,
    "keywords": DEFAULT_KEYWORDS,
    "alert_on_bot_mention": True,
    "alert_on_keywords": True,
    "forward_alert_messages": False,
    "digest_enabled": True,
    "digest_hour": 9,
    "digest_max_messages": 40,
    "auto_reply_groups_only_when_mentioned": True,
}


def default_group_watch_config() -> dict[str, Any]:
    return dict(DEFAULT_CONFIG)


def normalize_group_watch_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    cfg = default_group_watch_config()
    if not isinstance(raw, dict):
        return cfg
    cfg.update({k: v for k, v in raw.items() if k in cfg or k in ("watched_chats",)})
    if not isinstance(cfg.get("watched_chats"), list):
        cfg["watched_chats"] = []
    if not isinstance(cfg.get("keywords"), list):
        cfg["keywords"] = list(DEFAULT_KEYWORDS)
    cfg["keywords"] = [str(k).strip() for k in cfg["keywords"] if str(k).strip()]
    if cfg.get("owner_chat_id") is not None:
        cfg["owner_chat_id"] = str(cfg["owner_chat_id"])
    return cfg


def get_group_watch_from_meta(meta: dict[str, Any] | None) -> dict[str, Any]:
    return normalize_group_watch_config((meta or {}).get(META_KEY))


async def _redis() -> Any:
    import redis.asyncio as aioredis

    url = getattr(settings, "MESSENGER_REDIS_URL", None) or settings.REDIS_URL
    return aioredis.from_url(url)


def _is_group_chat(chat_type: str | None) -> bool:
    return chat_type in {"group", "supergroup"}


def _watched_chat_ids(cfg: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for item in cfg.get("watched_chats") or []:
        if isinstance(item, dict) and item.get("chat_id") is not None:
            ids.add(str(item["chat_id"]))
        elif item is not None and not isinstance(item, dict):
            ids.add(str(item))
    return ids


def is_chat_watched(cfg: dict[str, Any], chat_id: int | str, chat_type: str | None) -> bool:
    if not cfg.get("enabled"):
        return False
    if not _is_group_chat(chat_type):
        return False
    if cfg.get("watch_all_groups", True):
        return True
    return str(chat_id) in _watched_chat_ids(cfg)


def upsert_watched_chat(
    cfg: dict[str, Any],
    *,
    chat_id: int | str,
    title: str = "",
    chat_type: str = "supergroup",
) -> dict[str, Any]:
    chats = list(cfg.get("watched_chats") or [])
    sid = str(chat_id)
    found = False
    for item in chats:
        if isinstance(item, dict) and str(item.get("chat_id")) == sid:
            item["title"] = title or item.get("title") or ""
            item["type"] = chat_type or item.get("type") or "supergroup"
            item["updated_at"] = datetime.now(UTC).isoformat()
            found = True
            break
    if not found:
        chats.append(
            {
                "chat_id": sid,
                "title": title or "",
                "type": chat_type or "supergroup",
                "added_at": datetime.now(UTC).isoformat(),
            }
        )
    cfg["watched_chats"] = chats
    return cfg


def bot_mentioned(text: str, entities: list[Any], bot_username: str | None) -> bool:
    if not bot_username:
        return False
    uname = bot_username.lstrip("@").lower()
    if f"@{uname}" in (text or "").lower():
        return True
    for ent in entities or []:
        if not isinstance(ent, dict):
            continue
        if ent.get("type") == "mention":
            offset = int(ent.get("offset") or 0)
            length = int(ent.get("length") or 0)
            frag = (text or "")[offset : offset + length].lstrip("@").lower()
            if frag == uname:
                return True
        if ent.get("type") == "text_mention":
            user = ent.get("user") or {}
            if str(user.get("username") or "").lower() == uname:
                return True
    return False


def matching_keywords(text: str, keywords: list[str]) -> list[str]:
    lowered = (text or "").lower()
    hits: list[str] = []
    for kw in keywords:
        k = kw.lower().strip()
        if not k:
            continue
        if re.search(rf"(?<!\w){re.escape(k)}(?!\w)", lowered) or k in lowered:
            hits.append(kw)
    return hits


async def buffer_group_message(
    account_id: str,
    chat_id: int | str,
    entry: dict[str, Any],
) -> None:
    try:
        r = await _redis()
        key = _BUF_KEY.format(account_id=account_id, chat_id=chat_id)
        payload = json.dumps(entry, ensure_ascii=False)
        await r.lpush(key, payload)
        await r.ltrim(key, 0, _MAX_BUFFER - 1)
        await r.expire(key, _BUF_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Telegram group buffer failed: %s", exc)


async def list_buffered_messages(
    account_id: str,
    chat_id: int | str,
    *,
    limit: int = 40,
) -> list[dict[str, Any]]:
    try:
        r = await _redis()
        key = _BUF_KEY.format(account_id=account_id, chat_id=chat_id)
        raw = await r.lrange(key, 0, max(0, limit - 1))
        out: list[dict[str, Any]] = []
        for item in raw or []:
            try:
                if isinstance(item, bytes):
                    item = item.decode()
                data = json.loads(item)
                if isinstance(data, dict):
                    out.append(data)
            except Exception:
                continue
        return out
    except Exception as exc:
        logger.warning("Telegram group buffer read failed: %s", exc)
        return []


async def _alert_cooldown_ok(account_id: str, chat_id: str, fingerprint: str) -> bool:
    try:
        r = await _redis()
        key = _ALERT_CD_KEY.format(
            account_id=account_id, chat_id=chat_id, fingerprint=fingerprint[:64]
        )
        created = await r.set(key, "1", nx=True, ex=_ALERT_COOLDOWN_SECONDS)
        return bool(created)
    except Exception:
        return True


async def notify_owner(
    client: TelegramAPIClient,
    owner_chat_id: str,
    text: str,
) -> bool:
    try:
        await client.send_message(owner_chat_id, text)
        return True
    except (TelegramAPIError, ValueError) as exc:
        logger.warning("Telegram owner notify failed: %s", exc)
        return False


async def process_group_message_watch(
    *,
    client: TelegramAPIClient,
    account_id: str,
    cfg: dict[str, Any],
    inbound: dict[str, Any],
    bot_username: str | None,
) -> dict[str, Any]:
    """Buffer + optional instant alert for a watched group message."""
    result: dict[str, Any] = {"buffered": False, "alerted": False, "reason": ""}
    chat_id = inbound.get("chat_id")
    chat_type = inbound.get("chat_type")
    if not is_chat_watched(cfg, chat_id, chat_type):
        result["reason"] = "not_watched"
        return result

    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "message_id": inbound.get("message_id"),
        "chat_id": str(chat_id),
        "chat_title": inbound.get("chat_title") or "",
        "sender_id": inbound.get("sender_id"),
        "sender_name": inbound.get("sender_name") or "",
        "sender_username": inbound.get("sender_username") or "",
        "text": (inbound.get("text") or "")[:1000],
    }
    await buffer_group_message(account_id, chat_id, entry)
    result["buffered"] = True

    owner = cfg.get("owner_chat_id")
    if not owner:
        result["reason"] = "no_owner"
        return result

    text = inbound.get("text") or ""
    entities = inbound.get("entities") or []
    mention = bool(
        cfg.get("alert_on_bot_mention") and bot_mentioned(text, entities, bot_username)
    )
    kw_hits = (
        matching_keywords(text, list(cfg.get("keywords") or []))
        if cfg.get("alert_on_keywords")
        else []
    )
    if not mention and not kw_hits:
        result["reason"] = "no_alert_match"
        return result

    fingerprint = f"{'m' if mention else ''}{','.join(kw_hits)}:{inbound.get('message_id')}"
    if not await _alert_cooldown_ok(account_id, str(chat_id), fingerprint):
        result["reason"] = "alert_cooldown"
        return result

    title = inbound.get("chat_title") or str(chat_id)
    who = inbound.get("sender_name") or inbound.get("sender_username") or "someone"
    reasons = []
    if mention:
        reasons.append("bot mention")
    if kw_hits:
        reasons.append("keywords: " + ", ".join(kw_hits[:5]))
    alert_text = (
        f"📣 Visibility group alert — {title}\n"
        f"From: {who}\n"
        f"Why: {'; '.join(reasons)}\n\n"
        f"{text[:1500]}"
    )
    if cfg.get("forward_alert_messages") and inbound.get("message_id") is not None:
        try:
            await client.forward_message(owner, chat_id, int(inbound["message_id"]))
            result["alerted"] = True
            result["reason"] = "forwarded"
            return result
        except (TelegramAPIError, ValueError, TypeError) as exc:
            logger.info("forwardMessage failed, falling back to text: %s", exc)

    sent = await notify_owner(client, str(owner), alert_text)
    result["alerted"] = sent
    result["reason"] = "dm_sent" if sent else "dm_failed"
    return result


async def handle_owner_link_command(
    *,
    client: TelegramAPIClient,
    inbound: dict[str, Any],
    cfg: dict[str, Any],
    bot_username: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """If private /start or /linkowner, bind owner_chat_id. Returns (cfg, handled)."""
    if inbound.get("chat_type") != "private":
        return cfg, False
    text = (inbound.get("text") or "").strip()
    if not text:
        return cfg, False
    parts = text.split()
    cmd = parts[0].split("@", 1)[0].lower()
    link_cmds = {"/linkowner", "/watch"}
    # Bare /start and /start linkowner both link the owner for group-watch onboarding
    is_link = cmd in link_cmds or cmd == "/start"
    if not is_link:
        return cfg, False

    already = bool(cfg.get("owner_chat_id")) and str(cfg.get("owner_chat_id")) == str(
        inbound["chat_id"]
    )
    cfg = dict(cfg)
    cfg["owner_chat_id"] = str(inbound["chat_id"])
    cfg["owner_username"] = inbound.get("sender_username") or cfg.get("owner_username")
    cfg["enabled"] = True
    links = bot_deep_links(bot_username or "")
    add_link = links.get("add_to_group") or "t.me/<bot>?startgroup=watch"
    privacy = links.get("botfather_privacy") or "https://t.me/BotFather"
    if already:
        await notify_owner(
            client,
            str(cfg["owner_chat_id"]),
            "Already linked ✅\n\n"
            f"Still need: add me to the group\n{add_link}\n\n"
            f"And turn off Group Privacy in BotFather:\n{privacy}",
        )
    else:
        await notify_owner(
            client,
            str(cfg["owner_chat_id"]),
            "✅ Linked. I'll DM you keyword/mention alerts and daily digests.\n\n"
            f"1) Add me to Visibility Era 2.0:\n{add_link}\n"
            "(Telegram will ask you to pick the group.)\n\n"
            f"2) Disable Group Privacy:\n{privacy}\n"
            "→ /mybots → your bot → Bot Settings → Group Privacy → Turn off",
        )
    return cfg, True


# BotFather-only commands users sometimes send to our bot by mistake
_BOTFATHER_CMDS = {
    "/mybots",
    "/setprivacy",
    "/newbot",
    "/token",
    "/revoke",
    "/setname",
    "/setdescription",
    "/setabouttext",
    "/setuserpic",
    "/deletebot",
}


async def handle_mistaken_botfather_command(
    *,
    client: TelegramAPIClient,
    inbound: dict[str, Any],
) -> bool:
    """Redirect BotFather commands sent to our bot — do not AI-reply."""
    if inbound.get("chat_type") != "private":
        return False
    text = (inbound.get("text") or "").strip()
    if not text.startswith("/"):
        return False
    cmd = text.split()[0].split("@", 1)[0].lower()
    if cmd not in _BOTFATHER_CMDS:
        return False
    await notify_owner(
        client,
        str(inbound["chat_id"]),
        f"{cmd} is a @BotFather command, not for this bot.\n\n"
        "Open BotFather here:\nhttps://t.me/BotFather\n\n"
        "For Group Privacy: /mybots → Cloudless → Bot Settings → Group Privacy → Turn off\n\n"
        "To link alerts here, send /linkowner\n"
        "To add me to a group: https://t.me/Cloudless_newbot?startgroup=watch",
    )
    return True


def bot_deep_links(bot_username: str) -> dict[str, str]:
    """Official deep-link URLs (https://core.telegram.org/bots/features#deep-linking)."""
    uname = (bot_username or "").lstrip("@")
    if not uname:
        return {}
    return {
        "link_owner": f"https://t.me/{uname}?start=linkowner",
        "add_to_group": f"https://t.me/{uname}?startgroup=watch",
        "bot_profile": f"https://t.me/{uname}",
        "botfather_privacy": "https://t.me/BotFather",
    }


async def ensure_bot_commands(client: TelegramAPIClient) -> bool:
    """Register slash commands so /linkowner appears in Telegram."""
    try:
        return await client.set_my_commands(
            [
                {"command": "linkowner", "description": "Link this chat for group alerts & digests"},
                {"command": "start", "description": "Start / link owner for group watch"},
                {"command": "watch", "description": "Same as /linkowner"},
            ]
        )
    except (TelegramAPIError, ValueError) as exc:
        logger.warning("setMyCommands failed: %s", exc)
        return False


async def handle_bot_membership_change(
    *,
    client: TelegramAPIClient,
    cfg: dict[str, Any],
    event: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """When bot is added to a group, register it and optionally notify owner."""
    info: dict[str, Any] = {"registered": False, "notified": False}
    chat_type = event.get("chat_type")
    if not _is_group_chat(chat_type):
        return cfg, info

    new_status = event.get("new_status") or ""
    old_status = event.get("old_status") or ""
    if new_status not in {"member", "administrator"}:
        return cfg, info

    joined = old_status in {"left", "kicked", ""} or old_status is None
    promoted = old_status == "member" and new_status == "administrator"

    cfg = upsert_watched_chat(
        dict(cfg),
        chat_id=event["chat_id"],
        title=event.get("chat_title") or "",
        chat_type=chat_type or "supergroup",
    )
    cfg["enabled"] = True
    info["registered"] = True

    owner = cfg.get("owner_chat_id")
    if owner and (joined or promoted):
        title = event.get("chat_title") or event.get("chat_id")
        sent = await notify_owner(
            client,
            str(owner),
            f"👀 Now watching group: {title}\nchat_id={event['chat_id']}\nstatus={new_status}",
        )
        info["notified"] = sent
    return cfg, info


def should_auto_reply_in_group(
    cfg: dict[str, Any],
    inbound: dict[str, Any],
    bot_username: str | None,
) -> bool:
    if not _is_group_chat(inbound.get("chat_type")):
        return True
    if not cfg.get("auto_reply_groups_only_when_mentioned", True):
        return True
    return bot_mentioned(
        inbound.get("text") or "",
        inbound.get("entities") or [],
        bot_username,
    )


def build_digest_text(
    *,
    chat_title: str,
    chat_id: str,
    messages: list[dict[str, Any]],
    max_messages: int = 40,
) -> str:
    msgs = list(reversed(messages[:max_messages]))  # oldest → newest for reading
    lines = [
        f"📋 Group digest — {chat_title or chat_id}",
        f"{len(msgs)} message(s) in buffer (last ~48h)",
        "",
    ]
    for m in msgs[-max_messages:]:
        who = m.get("sender_name") or m.get("sender_username") or "?"
        text = (m.get("text") or "").replace("\n", " ")
        if len(text) > 180:
            text = text[:177] + "…"
        lines.append(f"• {who}: {text}")
    body = "\n".join(lines)
    if len(body) > MAX_MESSAGE_CHARS:
        body = body[: MAX_MESSAGE_CHARS - 20] + "\n…(truncated)"
    return body


async def send_digest_for_account(
    *,
    client: TelegramAPIClient,
    account_id: str,
    cfg: dict[str, Any],
    force: bool = False,
    hour_filter: int | None = None,
) -> dict[str, Any]:
    """Send digests for all watched chats to owner. hour_filter = local hour to match."""
    out: dict[str, Any] = {"sent": 0, "skipped": False, "reason": "", "chats": []}
    if not cfg.get("enabled") or not cfg.get("digest_enabled", True):
        out["skipped"] = True
        out["reason"] = "digest_disabled"
        return out
    owner = cfg.get("owner_chat_id")
    if not owner:
        out["skipped"] = True
        out["reason"] = "no_owner"
        return out

    tz_name = getattr(settings, "APP_TIMEZONE", None) or "Europe/Athens"
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("Europe/Athens")
    now_local = datetime.now(tz)
    digest_hour = int(cfg.get("digest_hour") or 9)
    if hour_filter is not None and not force and now_local.hour != hour_filter:
        out["skipped"] = True
        out["reason"] = "wrong_hour"
        return out
    if not force and now_local.hour != digest_hour:
        out["skipped"] = True
        out["reason"] = "wrong_hour"
        return out

    day_key = now_local.strftime("%Y-%m-%d")
    if not force:
        try:
            r = await _redis()
            sent_key = _DIGEST_SENT_KEY.format(account_id=account_id, day=day_key)
            created = await r.set(sent_key, "1", nx=True, ex=36 * 3600)
            if not created:
                out["skipped"] = True
                out["reason"] = "already_sent_today"
                return out
        except Exception:
            pass

    chat_ids = _watched_chat_ids(cfg)
    # If watch_all_groups and no explicit list, still digest known watched_chats only
    titles = {
        str(c.get("chat_id")): c.get("title") or ""
        for c in (cfg.get("watched_chats") or [])
        if isinstance(c, dict)
    }
    max_n = int(cfg.get("digest_max_messages") or 40)
    for cid in chat_ids:
        messages = await list_buffered_messages(account_id, cid, limit=max_n)
        if not messages:
            out["chats"].append({"chat_id": cid, "sent": False, "reason": "empty"})
            continue
        text = build_digest_text(
            chat_title=titles.get(cid, ""),
            chat_id=cid,
            messages=messages,
            max_messages=max_n,
        )
        ok = await notify_owner(client, str(owner), text)
        out["chats"].append({"chat_id": cid, "sent": ok, "count": len(messages)})
        if ok:
            out["sent"] += 1
    return out

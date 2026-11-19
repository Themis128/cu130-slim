"""Slack + ntfy -> telegram_channel_messages archive collector.

The notification surfaces all mirror the same ops traffic; this task
gives them the same archive+incident+resolution treatment the Telegram
lanes already have. Every 5 minutes it polls:

- **Slack** — ``conversations.history`` on the archive channel set
  (env ``SLACK_ARCHIVE_CHANNELS`` = comma-sep channel IDs; defaults to
  the SLACK_ALERTS/OPS/CHANNEL/TELEGRAM env IDs that resolve to real
  channel IDs). Rows land as ``slack:<channel-name>``.
- **ntfy** — ``GET /<topic>/json?poll=1&since=<cursor>`` on the archive
  topic set (env ``NTFY_ARCHIVE_TOPICS``, default the three alert
  topics). Auth = ``NTFY_READER_TOKEN`` (minted by the
  ntfy-reader-token workflow — alert-relay is write-only). Rows land
  as ``ntfy:<topic>``.
- **Gmail** — ``users.messages.list`` + ``get`` via OAuth refresh token
  (``GMAIL_OAUTH_CLIENT_ID`` / ``GMAIL_OAUTH_CLIENT_SECRET`` /
  ``GMAIL_REFRESH_TOKEN``). Query = ``EMAIL_ARCHIVE_QUERY``
  (default ``in:inbox``); incremental via an ``internalDate`` cursor
  with a 60s overlap — dedupe is by hashed Gmail message id. Rows
  land as ``email:<mailbox>`` (``EMAIL_ARCHIVE_MAILBOX``, default
  ``gmail``). First run backfills ``EMAIL_ARCHIVE_MAX_PAGES`` pages
  (default 10 x 100).

Cursors live in the ``notify_archive:cursor`` Redis hash — restart-safe.
``(channel, msg_id)`` unique constraint makes every write idempotent;
Slack ts -> microseconds, ntfy epoch seconds. The downstream
``telegram-incidents-ingest`` scans every channel in the table, so new
surfaces flow into incidents with zero extra wiring.
"""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_CURSOR_KEY = "notify_archive:cursor"
_SLACK_API = "https://slack.com/api"
_NTFY_BASE = "https://ntfy.cloudless.gr"
_DEFAULT_NTFY_TOPICS = "cloudless-ops,cloudless-alerts,nextcloud-ops"


def _slack_msg_id(ts: str) -> int:
    """Slack ts "1795087811.752809" -> exact µs int (fits signed bigint)."""
    return int(ts.replace(".", "")[:16].ljust(16, "0"))


def _sha_msg_id(key: str, fallback: int) -> int:
    """Opaque provider id -> sha1[:8] masked to signed bigint."""
    if not key:
        return fallback
    return int.from_bytes(hashlib.sha1(key.encode()).digest()[:8], "big") & 0x7FFFFFFFFFFFFFFF


def _ntfy_msg_id(nid: str, t: int) -> int:
    """ntfy 12-char base62 id -> sha1[:8] masked to signed bigint."""
    return _sha_msg_id(nid, t)


def _gmail_msg_id(mid: str, internal_ms: int) -> int:
    """Gmail message id -> sha1[:8] masked to signed bigint."""
    return _sha_msg_id(mid, internal_ms)


def _slack_channel_ids() -> list[str]:
    """Configured archive set: explicit override wins, else the known
    alert-channel env IDs."""
    explicit = os.environ.get("SLACK_ARCHIVE_CHANNELS")
    if explicit:
        return [c.strip() for c in explicit.split(",") if c.strip()]
    out: list[str] = []
    for env in (
        "SLACK_ALERTS_CHANNEL_ID",
        "SLACK_OPS_CHANNEL",
        "SLACK_CHANNEL_ID",
        "SLACK_TELEGRAM_CHANNEL_ID",
    ):
        v = (os.environ.get(env) or "").strip()
        if v.startswith(("C0", "G0")) and v not in out:
            out.append(v)
    return out


async def _slack_channel_name(client: httpx.AsyncClient, channel_id: str, cache: dict[str, str]) -> str:
    if channel_id in cache:
        return cache[channel_id]
    try:
        r = await client.get(
            f"{_SLACK_API}/conversations.info",
            params={"channel": channel_id},
            timeout=10,
        )
        name = (r.json().get("channel") or {}).get("name") or channel_id
    except Exception:  # noqa: BLE001
        name = channel_id
    cache[channel_id] = name
    return name


async def _insert_msg(db, model, team_id, channel: str, msg_id: int, date: datetime, sender: str, text: str, media: str) -> bool:
    """Idempotent insert — ON CONFLICT on (channel, msg_id)."""
    stmt = (
        pg_insert(model.__table__)
        .values(
            id=uuid.uuid4(),
            team_id=team_id,
            channel=channel[:32],
            msg_id=msg_id,
            date=date,
            sender=(sender or "")[:255],
            text=text[:8000],
            media=(media or "")[:64],
            created_at=datetime.now(UTC),
        )
        .on_conflict_do_nothing(constraint="uq_tg_msg_channel_msgid")
    )
    res = await db.execute(stmt)
    return bool(res.rowcount)


async def _collect_slack(db, r, model, stats: dict[str, Any], team_id) -> None:
    token = os.environ.get("SLACK_BOT_TOKEN") or ""
    if not token:
        stats["slack"] = "no SLACK_BOT_TOKEN"
        return
    channels = _slack_channel_ids()
    if not channels:
        stats["slack"] = "no channels"
        return
    names: dict[str, str] = {}
    pending: dict[str, str] = stats.setdefault("pending", {})
    async with httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as client:
        for cid in channels:
            chan = f"slack:{await _slack_channel_name(client, cid, names)}"
            cursor = await r.hget(_CURSOR_KEY, chan)
            oldest = cursor or "0"
            page_cur = ""
            # Backfill paginates until exhaustion (cap 10 pages/run — the
            # next beat picks up where the cursor landed).
            for _page in range(10):
                params: dict[str, Any] = {"channel": cid, "limit": 200}
                if oldest:
                    params["oldest"] = oldest
                if page_cur:
                    params["cursor"] = page_cur
                try:
                    resp = await client.get(f"{_SLACK_API}/conversations.history", params=params, timeout=15)
                    payload = resp.json()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("slack history failed %s: %s", chan, exc)
                    stats.setdefault("errors", []).append(f"{chan}: request")
                    break
                if not payload.get("ok"):
                    logger.warning("slack history %s: %s", chan, payload.get("error"))
                    stats.setdefault("errors", []).append(f"{chan}: {payload.get('error')}")
                    break
                messages = payload.get("messages") or []
                stats["scanned"] += len(messages)
                for m in messages:
                    ts = m.get("ts") or "0"
                    text = m.get("text") or ""
                    if not text and not m.get("attachments") and not m.get("blocks"):
                        continue
                    inserted = await _insert_msg(
                        db,
                        model,
                        team_id,
                        chan,
                        _slack_msg_id(ts),
                        datetime.fromtimestamp(float(ts), UTC),
                        str(m.get("user") or m.get("bot_id") or ""),
                        text,
                        "attachment" if m.get("attachments") else "",
                    )
                    stats["inserted" if inserted else "dupes"] += 1
                    if ts > oldest:
                        oldest = ts
                if oldest != (cursor or "0"):
                    pending[chan] = oldest
                page_cur = (payload.get("response_metadata") or {}).get("next_cursor") or ""
                if not page_cur or not messages:
                    break


async def _collect_ntfy(db, r, model, stats: dict[str, Any], team_id) -> None:
    token = os.environ.get("NTFY_READER_TOKEN") or ""
    if not token:
        stats["ntfy"] = "no NTFY_READER_TOKEN"
        return
    topics = [t.strip() for t in (os.environ.get("NTFY_ARCHIVE_TOPICS") or _DEFAULT_NTFY_TOPICS).split(",") if t.strip()]
    base = (os.environ.get("NTFY_READER_BASE") or _NTFY_BASE).rstrip("/")
    import json

    headers = {"Authorization": f"Bearer {token}", "User-Agent": "socialauto-archive/1.0"}
    async with httpx.AsyncClient(headers=headers) as client:
        for topic in topics:
            chan = f"ntfy:{topic}"
            cursor = int(await r.hget(_CURSOR_KEY, chan) or 0)
            try:
                resp = await client.get(
                    f"{base}/{topic}/json",
                    params={"poll": "1", "since": str(cursor) if cursor else "all"},
                    timeout=20,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ntfy poll failed %s: %s", chan, exc)
                stats.setdefault("errors", []).append(f"{chan}: request")
                continue
            if resp.status_code != 200:
                logger.warning("ntfy poll %s: http %s", chan, resp.status_code)
                stats.setdefault("errors", []).append(f"{chan}: http {resp.status_code}")
                continue
            newest = cursor
            for line in resp.text.strip().splitlines():
                if not line.strip():
                    continue
                try:
                    m = json.loads(line)
                except ValueError:
                    continue
                if m.get("event") != "message":
                    continue
                t = int(m.get("time") or 0)
                stats["scanned"] += 1
                # ntfy ids are unique 12-char base62 — hash to int64 so
                # same-second messages can't collide on (channel, msg_id).
                # The cursor (epoch-sec `since` window) is only a fetch
                # bound; dedupe is ON CONFLICT on the real id.
                msg_id = _ntfy_msg_id(m.get("id") or "", t)
                title = m.get("title") or ""
                text = (f"{title}\n{m.get('message', '')}" if title else m.get("message", "")).strip()
                inserted = await _insert_msg(
                    db,
                    model,
                    team_id,
                    chan,
                    msg_id,
                    datetime.fromtimestamp(t, UTC),
                    title or topic,
                    text,
                    "attachment" if m.get("attachment") else "",
                )
                stats["inserted" if inserted else "dupes"] += 1
                if t > newest:
                    newest = t
            if newest != cursor:
                stats.setdefault("pending", {})[chan] = str(newest)


async def _gmail_access_token(client: httpx.AsyncClient) -> str:
    r = await client.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": os.environ.get("GMAIL_OAUTH_CLIENT_ID", ""),
            "client_secret": os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", ""),
            "refresh_token": os.environ.get("GMAIL_REFRESH_TOKEN", ""),
            "grant_type": "refresh_token",
        },
        timeout=20,
    )
    return r.json()["access_token"]


async def _collect_email(db, r, model, stats: dict[str, Any], team_id) -> None:
    if not os.environ.get("GMAIL_REFRESH_TOKEN"):
        stats["email"] = "no GMAIL_REFRESH_TOKEN"
        return
    mailbox = os.environ.get("EMAIL_ARCHIVE_MAILBOX", "gmail")
    chan = f"email:{mailbox}"
    query = os.environ.get("EMAIL_ARCHIVE_QUERY", "in:inbox")
    max_pages = int(os.environ.get("EMAIL_ARCHIVE_MAX_PAGES", "10"))
    cursor = int(await r.hget(_CURSOR_KEY, chan) or 0)
    # 60s overlap — refetches dedupe via the hashed message id, so a
    # message landing in the cursor's own second can't be skipped.
    q = f"{query} after:{max(0, cursor // 1000 - 60)}" if cursor else query

    async with httpx.AsyncClient(timeout=20) as client:
        try:
            token = await _gmail_access_token(client)
        except Exception as exc:  # noqa: BLE001
            logger.warning("gmail token refresh failed: %s", exc)
            stats["email"] = "token refresh failed"
            return
        auth = {"Authorization": f"Bearer {token}"}
        newest = cursor
        page_tok = ""
        for _ in range(max_pages):
            params: dict[str, Any] = {"q": q, "maxResults": 100}
            if page_tok:
                params["pageToken"] = page_tok
            try:
                lst = (
                    await client.get(
                        "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                        params=params,
                        headers=auth,
                    )
                ).json()
            except Exception as exc:  # noqa: BLE001
                logger.warning("gmail list failed %s: %s", chan, exc)
                stats.setdefault("errors", []).append(f"{chan}: list")
                break
            ids = [m["id"] for m in lst.get("messages", []) if m.get("id")]
            stats["scanned"] += len(ids)
            for mid in ids:
                try:
                    full = (
                        await client.get(
                            f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{mid}",
                            params={
                                "format": "metadata",
                                "metadataHeaders": ["From", "Subject"],
                            },
                            headers=auth,
                        )
                    ).json()
                except Exception:  # noqa: BLE001
                    continue
                internal_ms = int(full.get("internalDate") or 0)
                if internal_ms and internal_ms <= cursor:
                    continue
                hdrs = {h.get("name", ""): h.get("value", "") for h in (full.get("payload") or {}).get("headers", [])}
                subject = hdrs.get("Subject", "")
                snippet = (full.get("snippet") or "").strip()
                text = f"{subject}\n{snippet}".strip()
                inserted = await _insert_msg(
                    db,
                    model,
                    team_id,
                    chan,
                    _gmail_msg_id(mid, internal_ms // 1000),
                    datetime.fromtimestamp(internal_ms / 1000, UTC),
                    hdrs.get("From", ""),
                    text,
                    "",
                )
                stats["inserted" if inserted else "dupes"] += 1
                if internal_ms > newest:
                    newest = internal_ms
            page_tok = lst.get("nextPageToken") or ""
            if not page_tok:
                break
        if newest != cursor:
            stats.setdefault("pending", {})[chan] = str(newest)


@celery_app.task(name="app.worker.tasks.notify_archive.notify_archive_ingest", bind=True, max_retries=1)
def notify_archive_ingest(self):
    async def _run() -> dict[str, Any]:
        import redis.asyncio as aioredis

        from app.core.config import get_settings
        from app.models.telegram_message import TelegramChannelMessage

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        stats: dict[str, Any] = {"scanned": 0, "inserted": 0, "dupes": 0}
        try:
            from sqlalchemy import select

            from app.models.social_account import SocialAccount

            async with task_session() as db:
                team_id = (await db.execute(select(SocialAccount.team_id).where(SocialAccount.platform == "telegram").limit(1))).scalar()
                await _collect_slack(db, r, TelegramChannelMessage, stats, team_id)
                await _collect_ntfy(db, r, TelegramChannelMessage, stats, team_id)
                await _collect_email(db, r, TelegramChannelMessage, stats, team_id)
                try:
                    await db.commit()
                    for chan, cur in (stats.pop("pending", {}) or {}).items():
                        await r.hset(_CURSOR_KEY, chan, cur)
                except Exception:  # noqa: BLE001
                    await db.rollback()
        finally:
            await r.aclose()
        return stats

    try:
        stats = run_async(_run())
        logger.info("notify-archive ingest: %s", stats)
        return stats
    except Exception:  # noqa: BLE001
        logger.exception("notify-archive ingest failed")
        raise self.retry(countdown=120)

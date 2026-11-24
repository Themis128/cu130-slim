"""Talk call-event ingest — chat system messages → analytics_events → datalake.

Nextcloud Talk writes call lifecycle into room chat as system messages
(``call_started``, ``call_ended`` with duration, ``call_left``, guest
join/leave variants). There is no call-events webhook, so a sweep reads
each room's recent history and lands new events in ``analytics_events``
— the existing ``export-datalake`` task carries them to the lake.

Dedupe key: ``platform_event_id = "<room_token>:<message_id>"``.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.models.analytics import AnalyticsEvent
from app.models.talk_room import TalkRoom

logger = logging.getLogger(__name__)

_OCS = "/ocs/v2.php/apps/spreed/api"
# systemMessage values that describe call participation/lifecycle
_CALL_SYS_RE = re.compile(r"^(call_|participant_)", re.IGNORECASE)
_DURATION_RE = re.compile(r"Duration\s+(?:(\d+):)?(\d{1,2}):(\d{2})")
_CHAT_LIMIT = 100


def talk_calls_enabled() -> bool:
    s = get_settings()
    return bool(s.NEXTCLOUD_USERNAME and s.NEXTCLOUD_APP_PASSWORD)


def _auth() -> tuple[str, str]:
    s = get_settings()
    return (s.NEXTCLOUD_USERNAME, s.NEXTCLOUD_APP_PASSWORD)


def _base() -> str:
    s = get_settings()
    base = (getattr(s, "NEXTCLOUD_BASE_URL", "") or "").strip().rstrip("/")
    if base:
        return base
    dav = (getattr(s, "NEXTCLOUD_DAV_URL", "") or "").strip()
    m = re.match(r"(https?://[^/]+)", dav)
    return m.group(1) if m else ""


async def _rooms(db) -> list[dict[str, str]]:
    """All Talk rooms worth scanning: bridge-mapped rooms + every room the
    service account has joined (covers the public /call/ guest room)."""
    tokens: dict[str, str] = {}
    rows = (await db.execute(select(TalkRoom.room_token, TalkRoom.team_id))).all()
    for token, team_id in rows:
        tokens.setdefault(token, str(team_id))

    base = _base()
    if base:
        try:
            async with httpx.AsyncClient(timeout=20, auth=_auth()) as client:
                resp = await client.get(
                    f"{base}{_OCS}/v4/room",
                    params={"format": "json"},
                    headers={"OCS-APIRequest": "true", "Accept": "application/json"},
                )
            if resp.status_code < 300:
                for room in resp.json().get("ocs", {}).get("data", []):
                    token = room.get("token")
                    if token:
                        tokens.setdefault(token, "")
        except Exception:
            logger.warning("talk-calls: room list fetch failed", exc_info=True)
    return [{"token": t, "team_id": tid} for t, tid in tokens.items()]


async def _chat_messages(client: httpx.AsyncClient, base: str, token: str) -> list[dict]:
    resp = await client.get(
        f"{base}{_OCS}/v1/chat/{token}",
        params={"lookIntoFuture": "0", "limit": str(_CHAT_LIMIT)},
        headers={"OCS-APIRequest": "true", "Accept": "application/json"},
    )
    if resp.status_code >= 300:
        return []
    return resp.json().get("ocs", {}).get("data", []) or []


def _parse_duration(text: str) -> int | None:
    m = _DURATION_RE.search(text or "")
    if not m:
        return None
    h, mm, ss = int(m.group(1) or 0), int(m.group(2)), int(m.group(3))
    return h * 3600 + mm * 60 + ss


def _actor(msg: dict) -> dict[str, str]:
    params = msg.get("messageParameters") or {}
    actor = params.get("actor") or {}
    return {
        "actor_id": actor.get("id") or msg.get("actorId") or "",
        "actor_name": actor.get("name") or msg.get("actorDisplayName") or "",
        "actor_type": actor.get("type") or msg.get("actorType") or "",
    }


async def ingest_call_events(*, session_factory=None) -> dict[str, Any]:
    """One sweep: scan room chats for call system messages → analytics_events."""
    if not talk_calls_enabled():
        return {"skipped": "nextcloud talk not configured"}

    from app.worker._async import task_session

    sf = session_factory or task_session
    base = _base()
    if not base:
        return {"skipped": "no talk base url"}

    stats = {"rooms": 0, "events": 0, "duplicates": 0, "errors": 0}
    async with sf() as db:
        rooms = await _rooms(db)
        # default team for rooms without a TalkRoom row (public /call room)
        from app.models.user import Team

        default_team = (await db.execute(select(Team.id).limit(1))).scalar_one_or_none()
        if default_team is None:
            return {"skipped": "no teams"}

        for room in rooms:
            token = room["token"]
            team_id = room["team_id"] or str(default_team)
            try:
                async with httpx.AsyncClient(timeout=25, auth=_auth()) as client:
                    msgs = await _chat_messages(client, base, token)
            except Exception:
                stats["errors"] += 1
                logger.warning("talk-calls: chat fetch failed for %s", token, exc_info=True)
                continue
            stats["rooms"] += 1

            candidates = []
            for m in msgs:
                sm = m.get("systemMessage") or ""
                if not _CALL_SYS_RE.match(sm):
                    continue
                mid = m.get("id")
                if mid is None:
                    continue
                candidates.append((f"{token}:{mid}", m, sm))
            if not candidates:
                continue

            existing = set(
                (
                    await db.execute(
                        select(AnalyticsEvent.platform_event_id).where(
                            AnalyticsEvent.platform == "talk",
                            AnalyticsEvent.platform_event_id.in_([c[0] for c in candidates]),
                        )
                    )
                )
                .scalars()
                .all()
            )

            for peid, m, sm in candidates:
                if peid in existing:
                    stats["duplicates"] += 1
                    continue
                ts = m.get("timestamp")
                actor = _actor(m)
                text = m.get("message") or ""
                db.add(
                    AnalyticsEvent(
                        team_id=team_id,
                        event_type=f"talk_call_{sm}",
                        platform="talk",
                        platform_event_id=peid,
                        occurred_at=datetime.fromtimestamp(int(ts), UTC) if ts else datetime.now(UTC),
                        meta_data={
                            "room_token": token,
                            "room_name": m.get("displayName") or "",
                            "system_message": sm,
                            "actor_id": actor["actor_id"],
                            "actor_name": actor["actor_name"],
                            "actor_type": actor["actor_type"],
                            "duration_seconds": _parse_duration(text),
                            "message": text[:500],
                        },
                    )
                )
                stats["events"] += 1
        await db.commit()
    return stats

"""Inbox snapshots — persist the unified inbox for reporting.

The unified inbox aggregates live provider APIs (Messenger, personal
Messenger, Instagram DMs, WhatsApp) with no durable store, so the daily
lead-pulse report couldn't answer "how many conversations are unread /
unanswered right now" across all channels. This service:

- ``capture`` — runs the same fetchers as the inbox API and writes one
  ``inbox_snapshots`` batch per team. Called by a 6h beat task AND inline
  by the lead-pulse report (fresh data + history in one pass).
- ``latest_summary`` — per-platform unread/unanswered counts from the
  newest batch.
- ``whatsapp_frt_stats`` — first-response-time stats computed from
  ``whatsapp_messages`` timestamps (the only channel with message-level
  persistence): median + slowest reply + SLA breach counts.
"""

from __future__ import annotations

import logging
import statistics
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.inbox_snapshot import InboxSnapshot
from app.models.whatsapp_message import WhatsAppMessage

logger = logging.getLogger(__name__)

# First-response SLA tiers (research: DM questions ~1h, complaints ~30min;
# we run a single SMB operation so one business-hours-ish target applies).
FRT_TARGET_SECONDS = 4 * 3600   # 4h — generous SMB target
FRT_HARD_SECONDS = 24 * 3600    # 24h — anything past this is a breach


@dataclass
class InboxSummary:
    batch_id: str | None = None
    captured_at: str | None = None
    total: int = 0
    by_platform: dict[str, dict[str, int]] = field(default_factory=dict)
    unread_conversations: list[dict[str, str]] = field(default_factory=list)

    def platform_line(self) -> list[str]:
        lines = []
        for plat, counts in sorted(self.by_platform.items()):
            lines.append(
                f"• {plat}: {counts.get('total', 0)} threads, "
                f"{counts.get('unread', 0)} unread"
            )
        return lines or ["no snapshot data"]


async def capture(db: AsyncSession, team_id: uuid.UUID) -> dict[str, Any]:
    """Fetch all conversations and persist one snapshot batch."""
    from app.api.inbox import fetch_team_conversations

    batch_id = uuid.uuid4()
    conversations = await fetch_team_conversations(team_id)
    now = datetime.now(UTC)
    for c in conversations:
        db.add(InboxSnapshot(
            team_id=team_id,
            batch_id=batch_id,
            platform=c.platform,
            account_id=c.account_id,
            account_name=c.account_name,
            thread_id=c.thread_id,
            sender_name=c.sender_name,
            preview=(c.preview or "")[:500] or None,
            unread=c.unread,
            e2ee=c.e2ee,
            last_activity_at=c.timestamp,
            meta={"url": c.url} if c.url else {},
            captured_at=now,
        ))
    await db.commit()
    by_platform: dict[str, int] = {}
    for c in conversations:
        by_platform[c.platform] = by_platform.get(c.platform, 0) + 1
    logger.info("inbox snapshot %s: %d conversations %s", batch_id, len(conversations), by_platform)
    return {"batch_id": str(batch_id), "conversations": len(conversations), "by_platform": by_platform}


async def latest_summary(db: AsyncSession, team_id: uuid.UUID) -> InboxSummary:
    """Summarize the newest snapshot batch for a team."""
    newest = (await db.execute(
        select(func.max(InboxSnapshot.captured_at))
        .where(InboxSnapshot.team_id == team_id)
    )).scalar()
    if newest is None:
        return InboxSummary()

    rows = (await db.execute(
        select(InboxSnapshot)
        .where(InboxSnapshot.team_id == team_id,
               InboxSnapshot.captured_at == newest)
        .order_by(InboxSnapshot.platform, InboxSnapshot.sender_name)
    )).scalars().all()

    summary = InboxSummary(batch_id=str(rows[0].batch_id) if rows else None,
                           captured_at=newest.isoformat(), total=len(rows))
    for r in rows:
        p = summary.by_platform.setdefault(r.platform, {"total": 0, "unread": 0})
        p["total"] += 1
        if r.unread:
            p["unread"] += 1
            summary.unread_conversations.append({
                "platform": r.platform,
                "sender": r.sender_name or r.thread_id or "?",
                "preview": (r.preview or "")[:80],
            })
    return summary


async def whatsapp_frt_stats(db: AsyncSession, *, since: datetime) -> dict[str, Any]:
    """First-response-time stats over WhatsApp threads (message-level data).

    For every inbound message, the FRT is the gap to the next outbound
    message in the same thread. Threads still awaiting a reply are counted
    separately as ``unanswered``.
    """
    msgs = (await db.execute(
        select(WhatsAppMessage.sender_phone, WhatsAppMessage.direction,
               WhatsAppMessage.created_at, WhatsAppMessage.sender_name)
        .where(WhatsAppMessage.created_at >= since)
        .order_by(WhatsAppMessage.sender_phone, WhatsAppMessage.created_at)
    )).all()

    # group by phone → ordered (direction, ts)
    threads: dict[str, list[tuple[str, datetime]]] = {}
    names: dict[str, str] = {}
    for phone, direction, ts, name in msgs:
        threads.setdefault(phone, []).append((direction, ts))
        if direction == "inbound" and name:
            names.setdefault(phone, name)

    frts: list[float] = []
    unanswered: list[dict[str, str]] = []
    now = datetime.now(UTC)
    for phone, events in threads.items():
        pending_inbound: datetime | None = None
        for direction, ts in events:
            if direction == "inbound":
                if pending_inbound is None:
                    pending_inbound = ts
            elif direction == "outbound" and pending_inbound is not None:
                frts.append((ts - pending_inbound).total_seconds())
                pending_inbound = None
        if pending_inbound is not None and (now - pending_inbound) > timedelta(hours=2):
            unanswered.append({
                "phone": phone,
                "name": names.get(phone, phone),
                "waiting_hours": f"{(now - pending_inbound).total_seconds() / 3600:.0f}h",
            })

    frts_sorted = sorted(f for f in frts if f >= 0)
    def _fmt(secs: float) -> str:
        return f"{secs/60:.0f}m" if secs < 3600 else f"{secs/3600:.1f}h"

    return {
        "threads": len(threads),
        "replies_measured": len(frts_sorted),
        "median_frt": _fmt(statistics.median(frts_sorted)) if frts_sorted else None,
        "p90_frt": _fmt(frts_sorted[int(len(frts_sorted) * 0.9)]) if frts_sorted else None,
        "slowest_frt": _fmt(frts_sorted[-1]) if frts_sorted else None,
        "within_target": sum(1 for f in frts_sorted if f <= FRT_TARGET_SECONDS),
        "breaches_24h": sum(1 for f in frts_sorted if f > FRT_HARD_SECONDS),
        "unanswered": unanswered[:10],
    }

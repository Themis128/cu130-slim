"""datalake_rewards — close the action→outcome loop.

The executor persists ``datalake_actions`` AnalyticsEvents when it runs a
signal action. This module scores those actions against real post metrics
(``post_analytics_snapshots``) and persists ``datalake_rewards`` events —
the reward signal a contextual-bandit-lite policy needs before it can
weight future action selection.

Only actions with a measurable outcome are scored today:
``content_topic`` drafts carry ``result.post_id`` → latest snapshot's
``engagement_rate`` is the reward. Other action types (audit_escalation,
devapp_health, platform_focus) have no per-action metric yet — they are
skipped rather than scored zero, so absence of evidence never reads as
negative evidence.

``action_weights`` feeds ``datalake_insights.pick_actions``: when enough
reward samples exist, underperforming action types stop being re-proposed.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text

from app.models import AnalyticsEvent

logger = logging.getLogger(__name__)

ACTIONS_TYPE = "datalake_actions"
REWARDS_TYPE = "datalake_rewards"
LOOKBACK_DAYS = 30
# Minimum reward samples before weights influence action selection —
# below this, a single fluke post would dominate the policy.
MIN_SAMPLES = 3
# content_topic drafts under this mean engagement_rate stop being proposed.
CONTENT_TOPIC_MIN_REWARD = 0.005


async def _action_events(db, team_id) -> list[AnalyticsEvent]:
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    return list(
        (
            await db.execute(
                select(AnalyticsEvent)
                .where(
                    AnalyticsEvent.team_id == team_id,
                    AnalyticsEvent.event_type == ACTIONS_TYPE,
                    AnalyticsEvent.occurred_at >= since,
                )
                .order_by(AnalyticsEvent.occurred_at.asc())
            )
        )
        .scalars()
        .all()
    )


async def _already_scored(db, team_id, action_event_id) -> bool:
    row = (
        await db.execute(
            text("SELECT 1 FROM analytics_events WHERE team_id=:t AND event_type=:e AND meta_data->>'action_event_id'=:a LIMIT 1"),
            {"t": team_id, "e": REWARDS_TYPE, "a": str(action_event_id)},
        )
    ).scalar_one_or_none()
    return row is not None


async def _latest_snapshot(db, team_id, post_id):
    from app.models.analytics import PostAnalyticsSnapshot

    return (
        await db.execute(
            select(PostAnalyticsSnapshot)
            .where(
                PostAnalyticsSnapshot.team_id == team_id,
                PostAnalyticsSnapshot.post_id == post_id,
            )
            .order_by(PostAnalyticsSnapshot.captured_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def score_actions(db, team_id) -> dict[str, Any]:
    """Score un-scored action events against post metrics; persist rewards.

    Returns ``{"scored": n, "pending": n, "skipped": n}``.
    """
    events = await _action_events(db, team_id)
    scored = pending = skipped = 0
    for ev in events:
        meta = ev.meta_data or {}
        result = meta.get("result") or {}
        post_id = result.get("post_id")
        if not post_id:
            skipped += 1  # no measurable outcome (non-content actions, errors)
            continue
        if await _already_scored(db, team_id, ev.id):
            skipped += 1
            continue
        try:
            post_uuid = uuid.UUID(str(post_id))
        except (ValueError, AttributeError, TypeError):
            skipped += 1  # malformed post_id in legacy action events
            continue
        snap = await _latest_snapshot(db, team_id, post_uuid)
        if snap is None:
            pending += 1  # draft exists but no metrics synced yet
            continue
        db.add(
            AnalyticsEvent(
                team_id=team_id,
                event_type=REWARDS_TYPE,
                platform="datalake",
                occurred_at=datetime.now(UTC),
                meta_data={
                    "action_event_id": str(ev.id),
                    "action_type": meta.get("action_type"),
                    "key": meta.get("key"),
                    "post_id": post_id,
                    "reward": snap.engagement_rate,
                    "engagement_rate": snap.engagement_rate,
                    "impressions": snap.impressions,
                    "scored_at": datetime.now(UTC).isoformat(),
                },
            )
        )
        scored += 1
    await db.commit()
    return {"scored": scored, "pending": pending, "skipped": skipped}


async def action_weights(db, team_id) -> dict[str, Any]:
    """Mean reward per action type over the lookback window.

    Returns ``{action_type: {"avg_reward": float, "n": int}}`` — only types
    with at least one scored reward appear.
    """
    since = datetime.now(UTC) - timedelta(days=LOOKBACK_DAYS)
    rows = (
        (
            await db.execute(
                text(
                    "SELECT meta_data->>'action_type' AS at, "
                    "(meta_data->>'reward')::float AS reward "
                    "FROM analytics_events WHERE team_id=:t AND event_type=:e "
                    "AND occurred_at >= :s"
                ),
                {"t": team_id, "e": REWARDS_TYPE, "s": since},
            )
        )
        .mappings()
        .all()
    )
    agg: dict[str, list[float]] = {}
    for r in rows:
        if r["at"] and r["reward"] is not None:
            agg.setdefault(r["at"], []).append(float(r["reward"]))
    return {at: {"avg_reward": sum(v) / len(v), "n": len(v)} for at, v in agg.items()}


def weights_gate(weights: dict[str, Any] | None) -> dict[str, bool]:
    """Pure decision helper for pick_actions — which action types are gated.

    A type is gated (suppressed) only when it has >= MIN_SAMPLES rewards and
    its mean reward is below the type's floor. content_topic's floor is
    CONTENT_TOPIC_MIN_REWARD; other types have no floor yet (always allowed).
    """
    weights = weights or {}
    gated: dict[str, bool] = {}
    ct = weights.get("content_topic")
    if ct and ct.get("n", 0) >= MIN_SAMPLES and ct.get("avg_reward", 1.0) < CONTENT_TOPIC_MIN_REWARD:
        gated["content_topic"] = True
    return gated

"""datalake_executor — turns datalake_insights actions into work.

Hops:
1. action → execution: content_topic → draft Post; audit_escalation →
   Telegram booking ping; platform_focus → scheduler weight event.
2. outcome → feedback: executed drafts are tagged with the action so post
   metrics join back on the next sweep.
3. outcome → training: every execution persists a `datalake_actions`
   AnalyticsEvent — the outcome row for future model/style training,
   carried by the existing analytics_events export.

Runs after the insights sweep (same 6h cadence, +25min offset).
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, func, select, text

from app.core.security import decrypt_token
from app.models import AnalyticsEvent, Post, SocialAccount
from app.services.slack_notifications import _post_ntfy_alert
from app.services.telegram_api import TelegramAPIClient
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

TELEGRAM_CHAT = "-1004436942504"
SIGNALS_TYPE = "datalake_signals"
ACTIONS_TYPE = "datalake_actions"


async def _pending_signals(db) -> list[tuple]:
    """Latest ``datalake_signals`` event per team within the dedup window.

    The insights sweep persists one event per team per run; executing only
    the single newest event starves every other team's actions. Latest-per-
    team is selected in SQL before any row cap so a sweep covering more
    teams than a flat LIMIT still reaches every team's newest event."""
    since = datetime.now(UTC) - timedelta(hours=24)
    team_col = AnalyticsEvent.meta_data["team_id"].astext
    latest = (
        select(team_col.label("tid"), func.max(AnalyticsEvent.occurred_at).label("mx"))
        .where(AnalyticsEvent.event_type == SIGNALS_TYPE, AnalyticsEvent.occurred_at >= since)
        .group_by(team_col)
        .subquery()
    )
    rows = (
        (
            await db.execute(
                select(AnalyticsEvent)
                .join(
                    latest,
                    and_(
                        team_col == latest.c.tid,
                        AnalyticsEvent.occurred_at == latest.c.mx,
                    ),
                )
                .where(AnalyticsEvent.event_type == SIGNALS_TYPE)
            )
        )
        .scalars()
        .all()
    )
    seen: set = set()
    out = []
    for ev in rows:
        meta = ev.meta_data or {}
        tid = meta.get("team_id")
        if not tid or tid in seen:
            continue
        seen.add(tid)
        out.append((tid, meta))
    return out


async def _already_executed(db, team_id, action_type: str, key: str) -> bool:
    """Dedup — skip if the same action key succeeded in the last 24h.

    Rows whose result carries an ``error`` do NOT dedup: a transient
    Telegram/Slack/DB failure must retry on the next sweep, not suppress
    the action for a day."""
    row = (
        await db.execute(
            text(
                "SELECT 1 FROM analytics_events WHERE event_type=:t AND team_id=:team "
                "AND meta_data->>'action_type'=:a AND meta_data->>'key'=:k "
                "AND meta_data->'result'->>'error' IS NULL "
                "AND occurred_at >= :s LIMIT 1"
            ),
            {"t": ACTIONS_TYPE, "team": team_id, "a": action_type, "k": key, "s": datetime.now(UTC) - timedelta(hours=24)},
        )
    ).scalar_one_or_none()
    return row is not None


async def _exec_content_topic(db, team_id, action) -> dict:
    """content_topic → a draft Post tagged for the feedback join."""
    topic = action["topic"]
    if await _already_executed(db, team_id, "content_topic", topic):
        return {"skipped": "already drafted <24h"}
    platform = action.get("platform") or "linkedin"
    user_id = (await db.execute(text("SELECT user_id FROM posts WHERE team_id=:t AND user_id IS NOT NULL LIMIT 1"), {"t": team_id})).scalar_one_or_none()
    post = Post(
        team_id=team_id,
        user_id=user_id,
        content_text=f"[draft] {topic} — write from this pain point",
        status="draft",
        meta_data={
            "source": "datalake_action",
            "action_type": "content_topic",
            "topic": topic,
            "platform": platform,
            "reason": action.get("reason", ""),
        },
    )
    db.add(post)
    await db.flush()
    return {"post_id": str(post.id), "platform": platform}


async def _exec_audit_escalation(db, team_id, action) -> dict:
    """audit_escalation → Telegram booking ping with the emails."""
    emails = [e for e in (action.get("emails") or []) if e]
    if not emails:
        return {"skipped": "no contactable emails"}
    key = ",".join(sorted(emails))[:200]
    if await _already_executed(db, team_id, "audit_escalation", key):
        return {"skipped": "already pinged <24h"}
    acc = (await db.execute(select(SocialAccount).where(SocialAccount.platform == "telegram", SocialAccount.status == "active").limit(1))).scalar_one()
    enc = acc.meta_data.get("bot_token_enc")
    tok = decrypt_token(enc.encode() if isinstance(enc, str) else enc) if enc else decrypt_token(acc.access_token_enc)
    text = "📞 Audit requests pending booking:\n" + "\n".join(emails[:8])
    await TelegramAPIClient(tok).send_message(TELEGRAM_CHAT, text)
    await _post_ntfy_alert("ops", text)
    return {"pinged": len(emails)}


def _exec_platform_focus(action) -> dict:
    """platform_focus → weight the scheduler's posting order."""
    return {"platform_focus": action.get("platform"), "weight": "boost"}


async def _exec_devapp_health(db, team_id, action) -> dict:
    """devapp_health → Slack alert; the heal lanes (token-refresh,
    session-healer, consent_healers) own the actual remediation.

    Deduped per platform+reason for 24h — a persistent condition like an
    exhausted X quota must not re-alert on every insights sweep."""
    from app.services.slack_notifications import post_alert_to_slack

    key = f"{action.get('platform')}:{action.get('reason', '')}"[:200]
    if await _already_executed(db, team_id, "devapp_health", key):
        return {"skipped": "already alerted <24h"}
    label = f"{action.get('platform')}/@{action.get('username')}" if action.get("username") else str(action.get("platform"))
    ok = await post_alert_to_slack(f"⚠️ devapp health — {label}: {action.get('reason', 'unknown')}")
    if not ok:
        return {"error": "slack_alert_failed"}
    return {"alerted": label}


_EXECUTORS = {
    "content_topic": _exec_content_topic,
    "audit_escalation": _exec_audit_escalation,
    "platform_focus": _exec_platform_focus,
    "devapp_health": _exec_devapp_health,
}


@celery_app.task(name="app.worker.tasks.datalake_executor.execute_actions")
def execute_actions() -> dict:
    return run_async(_run())


async def _run() -> dict:
    async with task_session() as db:
        pending = await _pending_signals(db)
    results = []
    team_errors = []
    teams_done = 0
    for tid_raw, signals in pending:
        actions = signals.get("actions") or []
        if not actions:
            continue
        # Each team commits in its own session — a failed flush rolls back
        # only that team instead of poisoning one shared transaction and
        # discarding every other team's drafts and action records.
        team_results = []
        try:
            team_id = uuid.UUID(tid_raw) if isinstance(tid_raw, str) else tid_raw
            async with task_session() as db:
                for action in actions:
                    fn: Any = _EXECUTORS.get(action.get("type"))
                    key = str(action.get("topic") or action.get("platform") or action.get("type"))[:200]
                    if action.get("type") == "devapp_health":
                        # match the dedup key _exec_devapp_health checks against
                        key = f"{action.get('platform')}:{action.get('reason', '')}"[:200]
                    elif action.get("type") == "audit_escalation":
                        # match the dedup key _exec_audit_escalation checks against
                        key = ",".join(sorted(e for e in (action.get("emails") or []) if e))[:200]
                    if not fn:
                        # unknown type (e.g. lead_source, ad_efficiency) — record
                        # the skip so dropped actions stay auditable in the lake
                        out = {"skipped": "no executor"}
                    else:
                        try:
                            out = await fn(db, team_id, action) if asyncio.iscoroutinefunction(fn) else fn(action)
                        except Exception as e:
                            out = {"error": str(e)[:120]}
                    db.add(
                        AnalyticsEvent(
                            team_id=team_id,
                            event_type=ACTIONS_TYPE,
                            platform="datalake",
                            occurred_at=datetime.now(UTC),
                            meta_data={"action_type": action["type"], "key": key, "reason": action.get("reason", ""), "result": out},
                        )
                    )
                    team_results.append({"type": action["type"], "team": str(team_id), **out})
                await db.commit()
            results.extend(team_results)
            teams_done += 1
        except Exception as e:
            logger.exception("datalake_executor team %s failed", tid_raw)
            team_errors.append({"team": str(tid_raw), "error": str(e)[:200]})
    if not results and not team_errors:
        return {"skipped": "no signals"}
    return {"executed": len(results), "teams": teams_done, "team_errors": team_errors, "results": results}

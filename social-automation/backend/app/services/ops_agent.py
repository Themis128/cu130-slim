"""Ops-agent service — the AgentOps loop for the fleet.

Pipeline per sweep: ingest is done by detectors via ``report_incident``;
this module triages open incidents — gathers evidence (docker-socket-proxy
container states + incident detail), runs an LLM root-cause pass through the
standard DMR→CF inference chain, applies the safety policy, executes only
whitelisted actions, verifies resolution and escalates recurrences to the
severity-routed Telegram channels.

Safety contract (mirrors the deploy-lock / healer ownership rules):
- The agent NEVER restarts containers or touches the stack — compose-watchdog
  owns that. Whitelisted actions are task-level only (re-run a Celery task,
  mark resolved, escalate).
- Max ``MAX_AGENT_ATTEMPTS`` actions per incident, then escalate.
- A recurrence after resolve bumps ``recurrence_count``; >=2 → critical.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import and_, case, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ops_incident import IncidentSeverity, IncidentStatus, OpsIncident

logger = logging.getLogger(__name__)

SOCKET_PROXY = os.environ.get("DOCKER_SOCKET_PROXY_URL", "http://docker-socket-proxy:2375")
MAX_AGENT_ATTEMPTS = 3
RESOLVE_AFTER = timedelta(hours=2)  # no new reports for this long → auto-resolve
MAX_OPEN_PER_SWEEP = 10
DECAY_AFTER = timedelta(hours=24)  # resolved this long ago → next report starts a fresh incident
LLM_TIMEOUT_S = 30  # cap the sweep's LLM verdict call; DMR being down must not pin ops_incidents locks

# Whitelisted task-level actions the agent may dispatch itself.
# name → celery task path. Read-only/sync tasks only — never publishing.
ALLOWED_RERUN_TASKS = {
    "dmr_health": "app.worker.tasks.dmr_health.check_dmr_health",
    "devapps_sync": "app.worker.tasks.devapps_sync.sync_devapps",
    "analytics_sync": "app.worker.tasks.analytics.sync_all_analytics",
}


def fingerprint(source: str, kind: str, target: str) -> str:
    return hashlib.sha256(f"{source}|{kind}|{target}".encode()).hexdigest()[:32]


async def report_incident(
    db: AsyncSession,
    *,
    source: str,
    kind: str,
    title: str,
    target: str = "",
    severity: str = "ops",
    detail: dict | None = None,
) -> OpsIncident:
    """Atomic upsert on the fingerprint unique key.

    A report against a resolved incident reopens it and bumps
    ``recurrence_count`` (>=2 forces critical); a report against a live
    incident refreshes ``last_seen``/``detail``. A report against a resolved
    incident older than DECAY_AFTER starts a fresh incident instead —
    recurrence_count/agent_attempts reset to 0 and first_seen becomes now,
    so periodic infra bounces never re-post stale seen=/recur= history.
    The ON CONFLICT clause mirrors scripts/ops_incident_host.sh — keep them in sync.
    """
    fp = fingerprint(source, kind, target)
    now = datetime.now(UTC)
    sev = IncidentSeverity(severity) if severity in IncidentSeverity._value2member_map_ else IncidentSeverity.OPS
    decay_cutoff = now - DECAY_AFTER
    stale_resolved = and_(
        OpsIncident.status == IncidentStatus.RESOLVED,
        OpsIncident.resolved_at < decay_cutoff,  # only RESOLVED rows have resolved_at
    )
    new_recur = case(
        (stale_resolved, 0),
        (OpsIncident.status == IncidentStatus.RESOLVED, OpsIncident.recurrence_count + 1),
        else_=OpsIncident.recurrence_count,
    )
    reset_attempts = case((stale_resolved, 0), else_=OpsIncident.agent_attempts)
    # ANY reopen (not just decayed ones) starts a new episode: first_seen=now
    # keeps alert text truthful — a within-window reopen of a week-old row
    # must not re-post with the original stale date.
    fresh_first_seen = case((OpsIncident.status == IncidentStatus.RESOLVED, now), else_=OpsIncident.first_seen)
    stmt = pg_insert(OpsIncident).values(
        id=uuid.uuid4(),
        fingerprint=fp,
        source=source,
        kind=kind,
        target=target,
        severity=sev,
        title=title[:500],
        detail=detail or {},
        status=IncidentStatus.OPEN,
        recurrence_count=0,
        agent_attempts=0,
        first_seen=now,
        last_seen=now,
        created_at=now,
        updated_at=now,
    )
    stmt = stmt.on_conflict_do_update(
        index_elements=["fingerprint"],
        set_={
            "last_seen": now,
            "updated_at": now,
            "title": stmt.excluded.title,
            "detail": OpsIncident.detail.op("||")(stmt.excluded.detail),
            "recurrence_count": new_recur,
            "agent_attempts": reset_attempts,
            "first_seen": fresh_first_seen,
            # reopen on: resolved (new episode) OR escalation to critical on a
            # live episode never alerted at critical — sweep must re-triage it
            # and the gate must allow exactly one critical alert
            "status": case(
                (OpsIncident.status == IncidentStatus.RESOLVED, IncidentStatus.OPEN),
                (
                    and_(
                        stmt.excluded.severity == IncidentSeverity.CRITICAL,
                        OpsIncident.status != IncidentStatus.OPEN,
                        OpsIncident.alerted_severity.is_distinct_from("critical"),
                    ),
                    IncidentStatus.OPEN,
                ),
                else_=OpsIncident.status,
            ),
            # Gate resets ONLY on reopen-from-resolved — a new episode earns
            # one fresh alert. Critical escalation deliberately keeps the ops
            # stamp: _alert_due ranks critical above it, so exactly one
            # critical alert posts, and once stamped 'critical' the reopen
            # condition above goes false — chronic critical reporters settle.
            "alerted_at": case(
                (OpsIncident.status == IncidentStatus.RESOLVED, None),
                else_=OpsIncident.alerted_at,
            ),
            "alerted_severity": case(
                (OpsIncident.status == IncidentStatus.RESOLVED, None),
                else_=OpsIncident.alerted_severity,
            ),
            "resolved_at": case(
                (OpsIncident.status == IncidentStatus.RESOLVED, None),
                else_=OpsIncident.resolved_at,
            ),
            "severity": case(
                (stale_resolved, stmt.excluded.severity),
                (stmt.excluded.severity == "critical", IncidentSeverity.CRITICAL),
                (OpsIncident.severity == IncidentSeverity.CRITICAL, IncidentSeverity.CRITICAL),
                (new_recur >= 2, IncidentSeverity.CRITICAL),
                else_=OpsIncident.severity,
            ),
        },
    )
    await db.execute(stmt)
    inc = (await db.execute(select(OpsIncident).where(OpsIncident.fingerprint == fp))).scalar_one()
    await db.flush()
    return inc


async def _container_states() -> dict[str, dict]:
    """Best-effort container inventory via the read-only docker-socket-proxy."""
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            resp = await client.get(f"{SOCKET_PROXY}/containers/json", params={"all": "1"})
            resp.raise_for_status()
            out = {}
            for c in resp.json():
                names = c.get("Names") or []
                for n in names:
                    out[n.lstrip("/")] = {
                        "status": c.get("Status", ""),
                        "state": c.get("State", ""),
                    }
            return out
    except Exception as exc:
        logger.info("docker-socket-proxy unavailable: %s", exc)
        return {}


def _build_prompt(inc: OpsIncident, containers: dict[str, dict]) -> str:
    from app.services.ops_runbooks import runbook_context

    target_state = ""
    if inc.target:
        for name, st in containers.items():
            if name == inc.target or name.endswith(inc.target):
                target_state = f"container={name} state={st}"
                break
    evidence = {
        "source": inc.source,
        "kind": inc.kind,
        "target": inc.target,
        "title": inc.title,
        "detail": inc.detail,
        "first_seen": inc.first_seen.isoformat() if inc.first_seen else None,
        "recurrence_count": inc.recurrence_count,
        "container_state": target_state or "unknown",
    }
    detail_text = inc.detail if isinstance(inc.detail, str) else json.dumps(inc.detail or {})
    runbooks = runbook_context(inc.kind or "", inc.target or "", inc.title or "", detail_text)
    return (
        "You are the ops-agent for a Docker Compose social-automation stack "
        "(social-api, 4 celery workers, n8n, postgres, redis, sidecars). "
        "A detector reported this incident:\n\n"
        f"{json.dumps(evidence, indent=2, default=str)[:4000]}\n\n"
        f"{runbooks}"
        "Reply with ONLY a JSON object, no prose:\n"
        '{"severity":"info|ops|critical",'
        '"hypothesis":"<one sentence root-cause guess>",'
        '"action":"none|rerun:<task-key>|resolve|escalate",'
        '"confidence":0.0-1.0,\n'
        '"summary":"<2-3 sentence human-readable RCA for the alert channel>"}\n'
        f"Allowed rerun task keys: {sorted(ALLOWED_RERUN_TASKS)}. "
        "Choose 'resolve' only if the evidence shows the condition is already "
        "healthy. Choose 'escalate' when a human decision is required."
    )


def _parse_verdict(text: str) -> dict:
    try:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1:
            return {}
        return json.loads(text[start : end + 1])
    except Exception:
        return {}


async def _llm_verdict(inc: OpsIncident, containers: dict[str, dict], db: AsyncSession) -> dict:
    from app.core.config import get_settings
    from app.services.inference import call_inference

    prompt = _build_prompt(inc, containers)
    result = await call_inference(
        prompt=prompt,
        provider_name="dmr",
        db=db,
        model_override=get_settings().DMR_MID_MODEL,
        max_tokens=400,
    )
    text = result.get("response") or result.get("text") or ""
    if isinstance(text, list):
        text = "\n".join(str(p) for p in text)
    return _parse_verdict(str(text))


async def _dispatch_rerun(task_key: str) -> str:
    path = ALLOWED_RERUN_TASKS.get(task_key)
    if not path:
        return f"unknown task key {task_key}"
    try:
        from celery import current_app

        current_app.send_task(path)
        return f"dispatched {path}"
    except Exception as exc:
        return f"dispatch failed: {exc}"


async def sweep(db: AsyncSession) -> dict:
    """One agent pass: auto-resolve stale incidents, then triage open ones."""
    now = datetime.now(UTC)
    stats = {"resolved_stale": 0, "triaged": 0, "actioned": 0, "escalated": 0, "errors": 0}

    # 1) auto-resolve incidents with no new reports within RESOLVE_AFTER
    stale = (
        (
            await db.execute(
                select(OpsIncident).where(
                    OpsIncident.status.in_([IncidentStatus.OPEN, IncidentStatus.TRIAGED, IncidentStatus.ACTIONED, IncidentStatus.ESCALATED]),
                    OpsIncident.last_seen < now - RESOLVE_AFTER,
                )
            )
        )
        .scalars()
        .all()
    )
    for inc in stale:
        inc.status = IncidentStatus.RESOLVED
        inc.resolved_at = now
        inc.action_result = (inc.action_result + " | " if inc.action_result else "") + "auto-resolved: no recurrence"
        stats["resolved_stale"] += 1

    await db.commit()  # release auto-resolve locks before any network call —
    # a report landing mid-flip must reopen the row, not be swallowed by it
    for inc in stale:
        await _edit_resolution_notice(inc)

    # 1b) grace-expired resolved alerts → delete the telegram message, clear refs
    try:
        purged, cleared = await _purge_resolved_alerts(db, now)
        if purged:
            stats["purged_alerts"] = purged
        if cleared:
            await db.commit()  # refs are cleared even on delete failure — persist that too
    except Exception as exc:
        logger.warning("ops-agent alert purge failed: %s", exc)

    # 2) triage open incidents, oldest first, capped per sweep
    open_incs = (
        (
            await db.execute(
                select(OpsIncident)
                .where(
                    or_(
                        OpsIncident.status == IncidentStatus.OPEN,
                        and_(
                            OpsIncident.status == IncidentStatus.TRIAGED,
                            OpsIncident.alerted_at.is_(None),
                        ),
                    )
                )
                .order_by(OpsIncident.first_seen)
                .limit(MAX_OPEN_PER_SWEEP)
            )
        )
        .scalars()
        .all()
    )
    if not open_incs:
        return stats  # auto-resolves already committed above

    containers = await _container_states()

    for inc in open_incs:
        # retry lane: already-triaged incidents whose alert never landed only
        # need the delivery re-attempted — re-running triage would burn an
        # agent attempt per retry and march them toward false escalation
        if inc.status == IncidentStatus.TRIAGED:
            await _alert(inc, "critical" if inc.severity == IncidentSeverity.CRITICAL else "ops")
            await db.commit()
            continue
        try:
            async with asyncio.timeout(LLM_TIMEOUT_S):
                verdict = await _llm_verdict(inc, containers, db)
        except Exception as exc:
            logger.warning("ops-agent LLM verdict failed for %s: %s", inc.fingerprint, exc)
            verdict = {}
        inc.agent_attempts += 1
        inc.rca = str(verdict.get("summary") or verdict.get("hypothesis") or "")[:4000]
        action = str(verdict.get("action") or "none")
        sev = str(verdict.get("severity") or "")
        if sev in IncidentSeverity._value2member_map_:
            inc.severity = IncidentSeverity(sev)
        inc.proposed_action = action[:200]

        # policy gate
        if inc.agent_attempts > MAX_AGENT_ATTEMPTS or inc.recurrence_count >= 2:
            inc.status = IncidentStatus.ESCALATED
            await _alert(inc, "critical" if inc.recurrence_count >= 2 else "ops")
            stats["escalated"] += 1
            await db.commit()  # per-incident: release row locks before the next LLM call
            continue

        taken, result_text = "none", ""
        if action.startswith("rerun:"):
            taken = action
            result_text = await _dispatch_rerun(action.split(":", 1)[1])
            inc.status = IncidentStatus.ACTIONED
            stats["actioned"] += 1
        elif action == "resolve":
            inc.status = IncidentStatus.RESOLVED
            inc.resolved_at = now
            inc.action_result = "agent marked resolved per verdict"
            stats["resolved_stale"] += 1
            await _edit_resolution_notice(inc)
            await db.commit()  # per-incident: release row locks before the next LLM call
            continue
        elif action == "escalate":
            inc.status = IncidentStatus.ESCALATED
            await _alert(inc, "ops")
            stats["escalated"] += 1
            await db.commit()  # per-incident: release row locks before the next LLM call
            continue
        else:
            inc.status = IncidentStatus.TRIAGED

        inc.action_taken = taken[:500]
        inc.action_result = (result_text or "triaged; awaiting recurrence or auto-resolve")[:4000]
        # alert on first triage or whenever the verdict escalates severity
        await _alert(inc, "critical" if inc.severity == IncidentSeverity.CRITICAL else "ops")
        stats["triaged"] += 1
        await db.commit()  # per-incident: release row locks before the next LLM call

    return stats


RESOLVED_KEEP = timedelta(hours=2)  # resolved-alert grace window before auto-delete
DELETE_BATCH = 20


async def _edit_resolution_notice(inc: OpsIncident) -> None:
    """One incident = one message: on resolve, edit the original alert in place
    to a one-line resolution instead of posting a new message."""
    if not inc.alert_chat_id or not inc.alert_message_id:
        return
    try:
        from app.services.slack_notifications import edit_telegram_alert

        text = f"\u2705 resolved: {inc.title} ({inc.source}/{inc.kind})"
        await edit_telegram_alert(inc.alert_chat_id, inc.alert_message_id, text)
        if inc.alerted_severity == "critical":
            # criticals are mirrored into ops as the permanent record — the mirror
            # message is untracked so it can't be edited; close the record with the
            # one-liner instead of leaving the firing message looking live forever
            try:
                from app.services.slack_notifications import post_ops_closure

                await post_ops_closure(text)
            except Exception as exc:
                logger.warning("ops-agent ops-mirror resolution post failed: %s", exc)
    except Exception as exc:
        logger.warning("ops-agent resolution edit failed: %s", exc)


async def _purge_resolved_alerts(db: AsyncSession, now: datetime) -> tuple[int, int]:
    """Auto-delete resolved incident alerts once the grace window passes —
    the channel keeps only live incidents. Best-effort, capped per sweep."""
    rows = (
        (
            await db.execute(
                select(OpsIncident)
                .where(
                    OpsIncident.status == IncidentStatus.RESOLVED,
                    OpsIncident.alert_message_id.is_not(None),
                    OpsIncident.resolved_at < now - RESOLVED_KEEP,
                )
                .limit(DELETE_BATCH)
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        return 0, 0
    from app.services.slack_notifications import delete_telegram_alert

    purged = 0
    for inc in rows:
        try:
            if await delete_telegram_alert(inc.alert_chat_id, inc.alert_message_id):
                purged += 1
        except Exception as exc:
            logger.warning("ops-agent alert delete failed for %s: %s", inc.fingerprint, exc)
        # clear refs either way — a delete failure (message already gone /
        # older than 48h api limit) must not retry forever
        inc.alert_chat_id = None
        inc.alert_message_id = None
    return purged, len(rows)


_SEV_RANK = {"info": 0, "ops": 1, "critical": 2}


def _alert_due(inc: OpsIncident, severity: str) -> bool:
    """Alert-once-per-episode gate: post only when never alerted this episode
    or when the new severity outranks the severity we last alerted at."""
    if inc.alerted_at is None:
        return True
    return _SEV_RANK.get(severity, 1) > _SEV_RANK.get(inc.alerted_severity or "", 0)


async def _alert(inc: OpsIncident, severity: str) -> None:
    if not _alert_due(inc, severity):
        return
    try:
        from app.services.slack_notifications import send_telegram_alert_tracked

        text = (
            f"[ops-agent/{severity}] {inc.title}\n"
            f"src={inc.source} kind={inc.kind} target={inc.target or '-'} "
            f"sev={inc.severity.value} seen={inc.first_seen:%Y-%m-%d %H:%M}Z "
            f"recur={inc.recurrence_count}\n"
            f"rca: {inc.rca[:600] or '(pending)'}\n"
            f"action: {inc.proposed_action or inc.action_taken or 'none'}" + (f" → {inc.action_result[:300]}" if inc.action_result else "")
        )
        sent, chat_id, message_id = await send_telegram_alert_tracked(severity, text)
        if sent:
            inc.alerted_at = datetime.now(UTC)
            inc.alerted_severity = severity
            if chat_id is not None and message_id is not None:
                if inc.alert_message_id is not None and (inc.alert_chat_id, inc.alert_message_id) != (chat_id, message_id):
                    # superseded alert (reopen new episode / severity escalation) —
                    # delete the old message so only the live receipt survives
                    try:
                        from app.services.slack_notifications import delete_telegram_alert

                        await delete_telegram_alert(inc.alert_chat_id, inc.alert_message_id)
                    except Exception as exc:
                        logger.warning("ops-agent superseded-alert delete failed: %s", exc)
                inc.alert_chat_id = chat_id
                inc.alert_message_id = message_id
        else:
            # delivery failed — leave the gate unstamped so a later sweep or
            # escalation pass can retry instead of swallowing the alert
            logger.warning("ops-agent alert send returned failure for %s", inc.fingerprint)
    except Exception as exc:
        logger.warning("ops-agent alert failed: %s", exc)

"""Telegram channel -> ops_incidents ingest bridge.

Reads new rows from ``telegram_channel_messages`` (populated by the
send-time archive + channel_post webhook + Telethon backfill), classifies
each alert-looking post, and feeds ``ops_agent.report_incident`` — the
5-minute ops-agent sweep then triages it (LLM RCA, whitelisted reruns,
severity-routed Telegram escalation).

Cursor per channel lives in Redis (``tg_ingest:cursor`` hash) — restart-safe,
idempotent, and cheap. Every channel present in the archive table is
scanned: ``#critical`` rows ingest as CRITICAL, everything else as OPS.
``#intel``/``#digest`` are informational and skipped by default
(``TG_INGEST_SKIP`` extends the skip set).
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

# Only alert-shaped posts become incidents; heartbeats/recovery notices don't.
_INCIDENT_RE = re.compile(
    r"(down|unhealthy|crash|restart.?loop|failed|failure|error|alert|"
    r"expired|expiring|revoked|unauthorized|401|403|429|500|502|503|530|"
    r"timeout|refused|unreachable|disk.*(full|pressure)|oom|flood|"
    r"\bp[12]\b|incident|outage|stale|drift|blocked|degraded)",
    re.IGNORECASE,
)
_RECOVERED_RE = re.compile(r"(recover|restored|back up|healthy again|resolved|fixed)", re.IGNORECASE)
# Message-level noise inside alert channels: digest/intel posts fanned
# out to ops topics, canary heartbeats, generated reports — archived,
# never incidents.
_SKIP_MSG_RE = re.compile(
    r"(^\s*\[(digest|intel|p3|p4)\]|^\s*canary ok|^\s*self-check ok|"
    r"daily report|daily summary|engagement sweep|drip sweep|"
    r"new jobs posted|\*published\*|clear skies|lead research sweep)",
    re.IGNORECASE,
)
_EMOJI_RE = re.compile(r":[a-z_]+:")  # strip :warning: :x: etc before target grab
_SEV_PREFIX_RE = re.compile(r"^\s*\[[a-z0-9 /_-]+\]", re.IGNORECASE)
# Backtick-quoted identifier first (Slack/ntfy "failed `app.worker.x.y`"),
# else first useful token ("social-api down" -> social-api)
_BACKTICK_RE = re.compile(r"`([a-zA-Z][a-zA-Z0-9_.-]{2,60})`")
_TARGET_RE = re.compile(r"\b([a-z][a-z0-9_-]{2,40})\b")
_GENERIC_TARGETS = {
    "cloudless", "ops", "alert", "alerts", "task", "worker", "failed",
    "warning", "error", "canary", "digest", "phone", "ntfy", "slack",
    "the", "and", "for", "new", "all", "self", "check", "test", "http",
    "https", "com", "gr", "service", "services", "system", "info",
}

# Every archived channel is scanned; severity is mapped by name with a
# sane default — informational digest channels are skipped by default
# (override with TG_INGEST_SKIP="name1,name2").
_CHANNEL_SEVERITY = {"critical": "critical", "ops": "ops"}
# v2 cursor = created_at epoch-ms. v1 used msg_id ordering which only
# works for monotonic ids (Telegram, Slack-ts) — ntfy/email rows hash
# their ids, so rows below a random-hash cursor were skipped forever.
_CURSOR_KEY = "tg_ingest:v2"
# Email surfaces carry newsletters/bulk mail whose subjects contain
# incident words ("I Failed Google…") — for `email:` channels only
# known ops senders classify (allowlist > denylist on a huge surface).
# Extend via TG_INGEST_EMAIL_SENDERS="acme-alerts,monitor.foo".
_EMAIL_ALERT_RE = re.compile(
    r"(vercel|github\.com|gitlab|cloudflare|uptime|kuma|socialauto|"
    r"docker|ntfy|sentry|pagerduty|datadog|grafana|prometheus|omv|"
    r"espocrm|stripe|paddle|polar|postiz|n8n|nextcloud|"
    r"alert|monitor|watchdog|cloudless)",
    re.IGNORECASE,
)
# Content/digest channels stay archived but never classify — their
# posts are reports, not alerts.
_SKIP_CHANNELS = {"intel", "digest", "slack:socialauto", "slack:socialauto-telegram"}


def _classify(channel: str, text: str) -> tuple[str, str] | None:
    """Return (kind, target) for alert-shaped posts, else None."""
    if not text or _RECOVERED_RE.search(text) or _SKIP_MSG_RE.search(text):
        return None
    if not _INCIDENT_RE.search(text):
        return None
    bt = _BACKTICK_RE.search(text)
    if bt:
        name = bt.group(1)
        target = ".".join(name.split(".")[-2:]) if "." in name else name
    else:
        body = _SEV_PREFIX_RE.sub("", _EMOJI_RE.sub(" ", text))
        target = next((tok for tok in _TARGET_RE.findall(body) if tok not in _GENERIC_TARGETS), "")
    kind = "infra_alert"
    t = text.lower()
    for needle, k in (
        ("unhealthy", "container_unhealthy"),
        ("restart", "container_restart_loop"),
        ("expired", "credential_expired"),
        ("token", "credential_expired"),
        ("503", "service_unavailable"),
        ("530", "tunnel_down"),
        ("disk", "disk_pressure"),
        ("oom", "oom_kill"),
        ("queue", "queue_backlog"),
    ):
        if needle in t:
            kind = k
            break
    return kind, target


@celery_app.task(name="app.worker.tasks.telegram_incidents.telegram_incidents_ingest", bind=True, max_retries=1)
def telegram_incidents_ingest(self):
    async def _run() -> dict[str, Any]:
        import redis.asyncio as aioredis
        from sqlalchemy import select

        from app.core.config import get_settings
        from app.models.telegram_message import TelegramChannelMessage
        from app.services.ops_agent import report_incident

        r = aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)
        stats: dict[str, Any] = {"scanned": 0, "ingested": 0, "skipped": 0}
        try:
            import os

            from sqlalchemy import distinct

            skip = set(_SKIP_CHANNELS) | {
                c.strip()
                for c in (os.environ.get("TG_INGEST_SKIP") or "").split(",")
                if c.strip()
            }
            async with task_session() as db:
                channels = set(_CHANNEL_SEVERITY) | {
                    row[0]
                    for row in (
                        await db.execute(select(distinct(TelegramChannelMessage.channel)))
                    ).all()
                    if row[0]
                }
                extra_senders = tuple(
                    s.strip()
                    for s in (os.environ.get("TG_INGEST_EMAIL_SENDERS") or "").split(",")
                    if s.strip()
                )
                email_alert = _EMAIL_ALERT_RE
                if extra_senders:
                    email_alert = re.compile(
                        _EMAIL_ALERT_RE.pattern + "|" + "|".join(re.escape(s) for s in extra_senders),
                        re.IGNORECASE,
                    )
                new_cursors: dict[str, int] = {}
                for channel in sorted(channels - skip):
                    severity = _CHANNEL_SEVERITY.get(channel, "ops")
                    cursor_ms = int(await r.hget(_CURSOR_KEY, channel) or 0)
                    is_email = channel.startswith("email:")
                    rows = (
                        await db.execute(
                            select(TelegramChannelMessage)
                            .where(
                                TelegramChannelMessage.channel == channel,
                                TelegramChannelMessage.created_at
                                > datetime.fromtimestamp(cursor_ms / 1000, UTC),
                            )
                            .order_by(TelegramChannelMessage.created_at)
                            .limit(200)
                        )
                    ).scalars().all()
                    for msg in rows:
                        stats["scanned"] += 1
                        ms = int(msg.created_at.timestamp() * 1000)
                        if ms > cursor_ms:
                            cursor_ms = ms
                        if is_email and not email_alert.search(msg.sender or ""):
                            stats["skipped"] += 1
                            continue
                        cls = _classify(channel, msg.text or "")
                        if not cls:
                            stats["skipped"] += 1
                            continue
                        kind, target = cls
                        await report_incident(
                            db,
                            source="telegram-channel",
                            kind=kind,
                            title=(msg.text or "")[:160],
                            target=target,
                            severity=severity,
                            detail={"channel": channel, "msg_id": msg.msg_id},
                        )
                        stats["ingested"] += 1
                    new_cursors[channel] = cursor_ms
                # Cursors land only after a durable commit — a crash
                # mid-run must re-scan, never skip.
                await db.commit()
                for chan, cur in new_cursors.items():
                    await r.hset(_CURSOR_KEY, chan, cur)
        finally:
            await r.aclose()
        return stats

    try:
        stats = run_async(_run())
        logger.info("telegram-incidents ingest: %s", stats)
        return stats
    except Exception:  # noqa: BLE001
        logger.exception("telegram-incidents ingest failed")
        raise self.retry(countdown=120)

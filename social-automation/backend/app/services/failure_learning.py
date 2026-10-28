"""Failure-trajectory learning — recurring error signatures → remediation proposals.

Adopted from the AOI pattern (Autonomous Operations Intelligence): every
publish/sync failure is a trajectory; when the *same signature* recurs past a
threshold the loop stops reporting and starts proposing a fix. The human stays
the approval gate (Agentic BPM) — proposals land in Slack + audit_logs, never
auto-applied.

Sources of trajectories: ``post_targets.error_message`` where
``status='failed'`` — the final resting state after max_attempts retries,
so every row is a real, human-relevant failure (not transient noise).
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models.content import PostTarget
from app.models.social_account import SocialAccount
from app.models.user import AuditLog

logger = logging.getLogger(__name__)

# ── Signature taxonomy ──────────────────────────────────────────────────────
# (signature_id, substring markers, bucket, remediation runbook pointer).
# Markers are matched case-insensitively against post_targets.error_message.
# Buckets follow the alert-triage model: platform-limit / app-bug / session /
# config / transient. Remediations name the owning skill/runbook so the
# proposal is actionable, not just "something failed".
SIGNATURES: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    (
        "tiktok_domain_unverified",
        ("url_ownership_unverified",),
        "config",
        "Verify the PULL_FROM_URL domain in TikTok for Developers → tiktok-console-ops runbook",
    ),
    (
        "tiktok_spam_risk",
        ("spam_risk", "spam risk"),
        "platform-limit",
        "Inbox-share API flagged — keep posts to DIRECT_POST/public drafts; no code fix",
    ),
    (
        "tiktok_publish_cancelled",
        ("publish_cancelled",),
        "transient",
        "TikTok-side share cancellation — user dismissed the draft or a duplicate share; not a code fault",
    ),
    (
        "media_public_url_missing",
        ("no public media urls", "media_public_base_url"),
        "config",
        "MEDIA_PUBLIC_BASE_URL unset — platforms that PULL_FROM_URL can't fetch media; set the public base URL",
    ),
    (
        "session_expired",
        ("session expired", "session_expired", "not logged in", "login_required", "browser session not logged in"),
        "session",
        "Re-authenticate the platform session — session-ops recovery ladder (cookie re-inject → credential login)",
    ),
    (
        "instagram_media_missing",
        ("media not available", "no media", "missing image", "media required", "at least one image"),
        "app-bug",
        "Media pipeline gap — the post published without an asset; check media generation + validate_media_for_platform",
    ),
    (
        "x_quota_exhausted",
        ("quota", "402", "credits depleted", "usage-capped"),
        "platform-limit",
        "X free-tier write quota — wait for monthly reset or publish via the browser fallback",
    ),
    (
        "meta_permissions",
        ("(#200)", "permissions error", "pages_manage_posts", "page access token"),
        "platform-limit",
        "Meta App Review scope missing — check developer-apps-ops for the required permission submission",
    ),
    (
        "threads_object_not_found",
        ("object not found", "media deleted", "not exist"),
        "transient",
        "Threads container/media expired before publish — retry regenerates the container",
    ),
    (
        "rate_limited",
        ("429", "rate limit", "too many requests", "try again later"),
        "transient",
        "Platform throttling — adaptive-rate-limiting backoff handles pacing; persistent 429s mean the session is flagged",
    ),
    (
        "unhandled_exception",
        ("unhandled exception", "traceback", "internal error"),
        "app-bug",
        "Code path crashed — reproduce the target row and file it; this bucket is always a bug",
    ),
)

_OTHER = ("other", (), "unknown", "Unmapped failure — review the error text and extend SIGNATURES if it recurs")

_ALERT_COOLDOWN = timedelta(days=3)
_WINDOW = timedelta(days=7)
_RECUR_THRESHOLD = 3


@dataclass
class FailureSignature:
    platform: str
    signature: str
    bucket: str
    remediation: str
    count: int
    latest_at: datetime
    samples: list[str] = field(default_factory=list)


def classify_error(error_text: str | None) -> tuple[str, str, str]:
    """error text → (signature_id, bucket, remediation). Unknown → 'other'."""
    low = (error_text or "").lower()
    for sig_id, markers, bucket, remediation in SIGNATURES:
        if any(m in low for m in markers):
            return sig_id, bucket, remediation
    return _OTHER[0], _OTHER[2], _OTHER[3]


async def _recent_failures(db, window: timedelta) -> list[tuple[str, str, datetime]]:
    """(platform, error_message, updated_at) for failed targets in the window."""
    q = (
        select(SocialAccount.platform, PostTarget.error_message, PostTarget.updated_at)
        .join(SocialAccount, PostTarget.social_account_id == SocialAccount.id)
        .where(
            PostTarget.status == "failed",
            PostTarget.error_message.isnot(None),
            PostTarget.updated_at >= datetime.now(UTC) - window,
        )
    )
    return [(r[0], r[1] or "", r[2]) for r in (await db.execute(q)).all()]


async def collect_signatures(db, window: timedelta = _WINDOW) -> dict[tuple[str, str], FailureSignature]:
    """Aggregate failed-target error messages into signature counters."""
    out: dict[tuple[str, str], FailureSignature] = {}
    for platform, error, ts in await _recent_failures(db, window):
        sig_id, bucket, remediation = classify_error(error)
        key = (platform, sig_id)
        if key not in out:
            out[key] = FailureSignature(platform, sig_id, bucket, remediation, 0, ts)
        sig = out[key]
        sig.count += 1
        if ts > sig.latest_at:
            sig.latest_at = ts
        if len(sig.samples) < 3 and error not in sig.samples:
            sig.samples.append(error[:300])
    return out


async def _recent_proposal_exists(db, team_id, platform: str, signature: str, cooldown: timedelta) -> bool:
    """Dedup: a proposal for this signature was already emitted inside the cooldown."""
    q = (
        select(AuditLog.id)
        .where(
            AuditLog.action == "failure-learning",
            AuditLog.resource_type == "signature",
            AuditLog.created_at >= datetime.now(UTC) - cooldown,
            AuditLog.meta["platform"].astext == platform,
            AuditLog.meta["signature"].astext == signature,
        )
        .limit(1)
    )
    return (await db.execute(q)).first() is not None


async def _record_proposal(db, team_id, sig: FailureSignature) -> None:
    db.add(
        AuditLog(
            team_id=team_id,
            action="failure-learning",
            resource_type="signature",
            resource_id=f"{sig.platform}:{sig.signature}",
            detail=f"{sig.count}x in 7d — {sig.remediation}",
            meta={
                "platform": sig.platform,
                "signature": sig.signature,
                "bucket": sig.bucket,
                "count": sig.count,
                "samples": sig.samples,
                "remediation": sig.remediation,
            },
        )
    )


async def run_failure_learning(
    db,
    team_id,
    *,
    window: timedelta = _WINDOW,
    threshold: int = _RECUR_THRESHOLD,
    cooldown: timedelta = _ALERT_COOLDOWN,
    post_slack: bool = True,
) -> dict[str, Any]:
    """Scan failure trajectories → emit remediation proposals for recurring signatures.

    Every run leaves an audit_logs artifact (``failure-learning`` /
    ``resource_type='sweep'``) — the worker's observable work product.
    Returns a report dict for the beat/log surface.
    """
    signatures = await collect_signatures(db, window)
    recurring = [s for s in signatures.values() if s.count >= threshold]
    proposed: list[FailureSignature] = []
    for sig in recurring:
        if await _recent_proposal_exists(db, team_id, sig.platform, sig.signature, cooldown):
            continue
        await _record_proposal(db, team_id, sig)
        proposed.append(sig)

    report: dict[str, Any] = {
        "scanned": sum(s.count for s in signatures.values()),
        "signatures": len(signatures),
        "recurring": len(recurring),
        "proposals": [
            {
                "platform": s.platform,
                "signature": s.signature,
                "bucket": s.bucket,
                "count": s.count,
                "remediation": s.remediation,
            }
            for s in proposed
        ],
        "by_bucket": dict(Counter(s.bucket for s in signatures.values())),
    }

    # Observable artifact — every run, even a quiet one.
    db.add(
        AuditLog(
            team_id=team_id,
            action="failure-learning",
            resource_type="sweep",
            detail=f"scanned={report['scanned']} signatures={report['signatures']} proposals={len(proposed)}",
            meta={
                "proposals": len(proposed),
                "recurring": len(recurring),
                "by_bucket": report["by_bucket"],
            },
        )
    )

    if post_slack and proposed:
        try:
            from app.services.slack_notifications import post_alert_to_slack

            lines = ["*Failure-trajectory loop — remediation proposals*"]
            for s in proposed:
                lines.append(f"• `{s.platform}` **{s.signature}** ({s.bucket}) — {s.count} failures in 7d\n  ↳ {s.remediation}")
            await post_alert_to_slack("\n".join(lines))
        except Exception:  # noqa: BLE001 — proposal rows already persisted
            logger.exception("failure learning: slack proposal failed")

    return report

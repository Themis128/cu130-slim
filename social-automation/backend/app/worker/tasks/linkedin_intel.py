"""Celery task: LinkedIn Ad Library weekly watch → intel digest.

Runs the watchlist (linkedin_intel_watch, auto-seeded with SMB/cloud
keywords + major cloud advertisers) against /rest/adLibrary, upserts
results into linkedin_ad_intel, flags ads that disappeared across
consecutive runs as "ended", and posts a compact digest to the #intel
Telegram channel.
"""

from __future__ import annotations

from typing import Any

from celery import shared_task

from app.services.linkedin_ad_library import run_watch
from app.services.slack_notifications import send_telegram_alert
from app.worker._async import run_async
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


def _format_digest(summary: dict[str, Any]) -> str:
    lines = [
        "LinkedIn Ad Library watch — weekly run",
        f"watches={summary.get('watches', 0)} new={summary.get('new', 0)} updated={summary.get('updated', 0)} ended={summary.get('ended', 0)}",
    ]
    for err in summary.get("errors", [])[:5]:
        lines.append(f"err: {err[:160]}")
    return "\n".join(lines)


async def _run() -> dict[str, Any]:
    summary = await run_watch()
    if "skip" in summary:
        return summary
    text = _format_digest(summary)
    await send_telegram_alert("intel", text)
    return summary


@shared_task(name="app.worker.tasks.linkedin_intel.run_linkedin_ad_library_watch")
def run_linkedin_ad_library_watch() -> dict[str, Any]:
    """Weekly Ad Library sweep + #intel digest (beat: Mon 09:00 Athens)."""
    return run_async(_run())

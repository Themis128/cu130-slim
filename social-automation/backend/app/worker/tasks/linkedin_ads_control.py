"""Celery task: LinkedIn ads pause/resume/status, triggered from Slack."""
from __future__ import annotations

import asyncio
from typing import Any

from celery import shared_task

from app.services.linkedin_ads_control import notify_slack, set_campaign_status
from app.services.linkedin_ads_report import collect_metrics
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


async def _run(action: str, response_url: str | None, user: str) -> dict[str, Any]:
    if action == "status":
        metrics = await collect_metrics()
        text = (
            f"LinkedIn campaign *{metrics.campaign_name or metrics.campaign_id}*: "
            f"status *{metrics.status}* · spend €{metrics.spend_eur:.2f} · "
            f"{metrics.clicks} clicks · CTR {metrics.ctr:.2f}%"
        )
        await notify_slack(text, response_url)
        return {"ok": True, "status": metrics.status}

    result = await set_campaign_status(action)
    verb = "pause" if action == "pause" else "resume"
    if result["ok"]:
        icon = ":pause_button:" if action == "pause" else ":arrow_forward:"
        text = f"{icon} LinkedIn campaign {verb}d by @{user} — {result['detail']}"
    else:
        text = (
            f":warning: Couldn't {verb} the LinkedIn campaign (requested by @{user}): "
            f"{result['detail']}"
        )
    await notify_slack(text, response_url)
    return result


@shared_task(name="app.worker.tasks.linkedin_ads_control.linkedin_ads_control")
def linkedin_ads_control(
    action: str, response_url: str | None = None, user: str = "someone"
) -> dict[str, Any]:
    return asyncio.run(_run(action, response_url, user))

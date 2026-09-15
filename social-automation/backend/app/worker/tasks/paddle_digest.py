"""Celery task: Paddle usage/revenue digest → Slack billing channel."""
from __future__ import annotations

import asyncio
from typing import Any

from celery import shared_task

from app.services.paddle_digest import send_paddle_digest_to_slack
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()


@shared_task(name="app.worker.tasks.paddle_digest.send_paddle_slack_digest")
def send_paddle_slack_digest(post_to_slack: bool = True) -> dict[str, Any]:
    """Build and deliver the Paddle usage/revenue digest."""
    return asyncio.run(send_paddle_digest_to_slack(post_to_slack=post_to_slack))

"""Nextcloud docs-sync — mirror repo documentation/architecture to Files.

Reads the doc manifest from GitHub master (worker containers don't mount
the checkout) and uploads changed markdown to ``SocialAuto/docs/repo/``
over WebDAV. Per-file blob SHAs in Redis make repeat runs cheap.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task

from app.services import nextcloud_docs
from app.worker._async import run_async

logger = logging.getLogger(__name__)


@shared_task(name="app.worker.tasks.nextcloud_docs.sync_nextcloud_docs_task", bind=True, max_retries=1)
def sync_nextcloud_docs_task(self) -> dict[str, Any]:
    if not nextcloud_docs.nextcloud_docs_enabled():
        return {"skipped": "nextcloud export disabled"}
    try:
        return run_async(nextcloud_docs.sync_nextcloud_docs())
    except Exception as exc:
        logger.warning("nextcloud docs sync failed: %s", exc)
        return {"error": str(exc)[:200]}

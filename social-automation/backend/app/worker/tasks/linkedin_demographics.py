"""Daily LinkedIn org demographics + page-stats sync.

Pulls the two org-analytics endpoints the Community Management token
already covers (``r_organization_followers`` / ``r_organization_social``)
and writes ``linkedin_org_follower_demographics`` /
``linkedin_org_page_stats`` AnalyticsEvents so the rows flow into the
datalake via ``export-datalake`` like every other devapp pull.

Self-gating on two levels, mirroring ``linkedin_portability_sync``:

1. **Scope pre-check** — skips accounts whose recorded grant lacks
   ``r_organization_followers`` (empty list = legacy → probe anyway).
2. **Live 401/403** — recorded as an ``api_error`` payload, no alerts;
   next run retries.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.analytics import AnalyticsEvent
from app.models.social_account import SocialAccount
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

API = "https://api.linkedin.com/v2"
VER = "202510"
REQUIRED_SCOPE = "r_organization_followers"
MAX_ELEMENTS_STORE = 200


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Linkedin-Version": VER,
        "X-Restli-Protocol-Version": "2.0.0",
    }


def _has_scope(account: SocialAccount) -> bool:
    granted = set(account.scopes or [])
    return not granted or REQUIRED_SCOPE in granted


def _org_urn(account: SocialAccount) -> str:
    meta = account.meta_data or {}
    urn = str(meta.get("author_urn") or "")
    if urn.startswith("urn:li:organization:"):
        return urn
    return f"urn:li:organization:{account.account_id}"


def _event(account: SocialAccount, event_type: str, payload: dict[str, Any]) -> AnalyticsEvent:
    return AnalyticsEvent(
        team_id=account.team_id,
        social_account_id=account.id,
        platform="linkedin",
        event_type=event_type,
        occurred_at=datetime.now(UTC),
        meta_data=payload,
    )


async def _get(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    r = await client.get(f"{API}/{path}", timeout=30)
    if r.status_code != 200:
        return {"status": r.status_code, "error": r.text[:300]}
    return {"status": 200, "data": r.json()}


async def _sync_account(db, account: SocialAccount) -> dict[str, Any]:
    if not _has_scope(account):
        return {"skipped": "missing-scope"}
    token = decrypt_token(bytes(account.access_token_enc))
    org = _org_urn(account).replace(":", "%3A")
    headers = _headers(token)
    now = datetime.now(UTC).isoformat()
    async with httpx.AsyncClient(headers=headers) as client:
        followers = await _get(
            client,
            f"organizationalEntityFollowerStatistics?q=organizationalEntity&organizationalEntity={org}",
        )
        page_stats = await _get(
            client,
            f"organizationPageStatistics?q=organization&organization={org}",
        )
    payload: dict = {"captured_at": now, "org_urn": org}
    if followers.get("status") == 200:
        payload["elements"] = (followers["data"].get("elements") or [])[:MAX_ELEMENTS_STORE]
        payload["count"] = len(payload["elements"])
    else:
        payload["error"] = followers.get("status") or followers.get("error")
    db.add(_event(account, "linkedin_org_follower_demographics", payload))

    stats_payload: dict = {"captured_at": now, "org_urn": org}
    if page_stats.get("status") == 200:
        stats_payload["elements"] = (page_stats["data"].get("elements") or [])[:MAX_ELEMENTS_STORE]
        stats_payload["count"] = len(stats_payload["elements"])
    else:
        stats_payload["error"] = page_stats.get("status") or page_stats.get("error")
    db.add(_event(account, "linkedin_org_page_stats", stats_payload))
    return {"followers": payload.get("count") or payload.get("error"), "page_stats": stats_payload.get("count") or stats_payload.get("error")}


@celery_app.task(name="app.worker.tasks.linkedin_demographics.sync_linkedin_demographics")
def sync_linkedin_demographics() -> dict[str, Any]:
    async def _run() -> dict[str, Any]:
        async with task_session() as db:
            accounts = (
                (
                    await db.execute(
                        select(SocialAccount).where(
                            SocialAccount.platform == "linkedin",
                            SocialAccount.account_type == "organization",
                        )
                    )
                )
                .scalars()
                .all()
            )
            if not accounts:
                accounts = (await db.execute(select(SocialAccount).where(SocialAccount.platform == "linkedin"))).scalars().all()
            results = {}
            for acc in accounts:
                try:
                    results[str(acc.id)] = await _sync_account(db, acc)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("linkedin demographics failed for %s: %s", acc.id, exc)
                    results[str(acc.id)] = {"error": str(exc)[:200]}
            await db.commit()
            return results

    return run_async(_run())

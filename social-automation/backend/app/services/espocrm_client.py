"""Read-side EspoCRM client — SocialAuto pulls client/contact info for campaigns.

EspoCRM runs on the omv k3s cluster (``espocrm`` namespace). The public URL
``espocrm.cloudless.gr`` sits behind Cloudflare Access, so the backend talks to
the cluster NodePort (``http://192.168.1.200:30700``) with a dedicated
``socialauto`` API user (``X-Api-Key``). Both values come from env:

    ESPOCRM_BASE_URL   e.g. http://192.168.1.200:30700
    ESPOCRM_API_KEY    ApiKey of the dedicated socialauto API user
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_PAGE = 200


def _cfg() -> tuple[str, str]:
    settings = get_settings()
    base = (getattr(settings, "ESPOCRM_BASE_URL", "") or "").strip().rstrip("/")
    key = (getattr(settings, "ESPOCRM_API_KEY", "") or "").strip()
    return base, key


def configured() -> bool:
    base, key = _cfg()
    return bool(base and key)


async def espo_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET ``{base}/api/v1{path}``. Raises on transport failure, returns the
    decoded body otherwise — including non-2xx, which callers inspect."""
    base, key = _cfg()
    if not base or not key:
        raise RuntimeError("EspoCRM not configured (ESPOCRM_BASE_URL/ESPOCRM_API_KEY)")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{base}/api/v1{path}",
            headers={"X-Api-Key": key},
            params=params or {},
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"EspoCRM GET {path} → {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"EspoCRM GET {path} → non-JSON body") from exc
    return data if isinstance(data, dict) else {"data": data}


async def list_all(
    entity: str,
    *,
    select: list[str] | None = None,
    where: list[dict[str, Any]] | None = None,
    order_by: str = "createdAt",
    order: str = "desc",
    max_items: int = 5000,
) -> list[dict[str, Any]]:
    """Walk an EspoCRM list endpoint with offset/maxSize pagination."""
    items: list[dict[str, Any]] = []
    offset = 0
    while len(items) < max_items:
        search: dict[str, Any] = {
            "offset": offset,
            "maxSize": min(_PAGE, max_items - len(items)),
            "orderBy": order_by,
            "order": order,
        }
        if select:
            search["select"] = select  # EspoCRM searchParams takes a JSON array
        if where:
            search["where"] = where
        data = await espo_get(f"/{entity}", params={"searchParams": json.dumps(search)})
        page = data.get("list") or []
        items.extend(page)
        if len(page) < _PAGE:
            break
        offset += len(page)
    return items[:max_items]


async def list_leads_for_campaign() -> list[dict[str, Any]]:
    """Leads eligible for marketing mail — not Converted/Dead, has an email."""
    return await list_all(
        "Lead",
        select=[
            "id", "firstName", "lastName", "emailAddress", "status",
            "source", "createdAt", "description",
        ],
        where=[
            {"type": "isNotNull", "attribute": "emailAddress"},
            {"type": "notEquals", "attribute": "status", "value": "Dead"},
            {"type": "notEquals", "attribute": "status", "value": "Converted"},
        ],
    )


async def list_contacts() -> list[dict[str, Any]]:
    return await list_all(
        "Contact",
        select=[
            "id", "firstName", "lastName", "emailAddress", "accountName",
            "accountId", "title", "doNotCall", "createdAt",
        ],
        where=[{"type": "isNotNull", "attribute": "emailAddress"}],
    )


async def list_accounts() -> list[dict[str, Any]]:
    return await list_all(
        "Account",
        select=["id", "name", "website", "industry", "type", "emailAddress", "createdAt"],
    )


async def espo_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``{base}/api/v1{path}`` — used for CRM activity writes (Notes)."""
    base, key = _cfg()
    if not base or not key:
        raise RuntimeError("EspoCRM not configured (ESPOCRM_BASE_URL/ESPOCRM_API_KEY)")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{base}/api/v1{path}",
            headers={"X-Api-Key": key, "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"EspoCRM POST {path} → {resp.status_code}: {resp.text[:200]}")
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {"data": data}


async def add_lead_note(espocrm_lead_id: str, note: str, *, entity: str = "Lead") -> bool:
    """Attach a Note to a CRM Lead/Contact — the survey-response activity
    record. Returns False instead of raising so a CRM outage never blocks a
    survey submission."""
    if not espocrm_lead_id or not configured():
        return False
    try:
        await espo_post(
            "/Note",
            {
                # Post-type Notes carry their body in `post` (not `note`).
                "type": "Post",
                "post": note[:10000],
                "parentType": entity,
                "parentId": espocrm_lead_id,
            },
        )
        return True
    except Exception:  # noqa: BLE001
        logger.exception("EspoCRM note attach failed for %s %s", entity, espocrm_lead_id)
        return False


async def lead_counts() -> dict[str, int]:
    """Counts by entity for the CRM access surface."""
    out: dict[str, int] = {}
    for entity in ("Lead", "Contact", "Account", "Opportunity"):
        try:
            data = await espo_get(f"/{entity}", params={"searchParams": '{"maxSize":0}'})
            out[entity.lower()] = int(data.get("total", 0))
        except Exception:  # noqa: BLE001
            out[entity.lower()] = -1
    return out

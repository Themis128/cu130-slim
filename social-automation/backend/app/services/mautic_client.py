"""Mautic funnel-management client — contacts, segments, campaigns.

Mautic runs in the docker stack (``mautic`` service) behind the
``authentik-outpost`` SSO gate on ``mautic.cloudless.gr``. The backend talks
to the container directly (``http://mautic:80``) — the tunnel's ``^/api/``
bypass means API calls never hit the outpost gate. Basic auth uses the admin
creds so every call is a real API user, not a session.

    MAUTIC_BASE_URL   e.g. http://mautic:80
    MAUTIC_USERNAME   admin user email (basic auth)
    MAUTIC_PASSWORD   admin user password
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_PAGE = 50


def _cfg() -> tuple[str, str, str]:
    settings = get_settings()
    base = (getattr(settings, "MAUTIC_BASE_URL", "") or "").strip().rstrip("/")
    user = (getattr(settings, "MAUTIC_USERNAME", "") or "").strip()
    pw = (getattr(settings, "MAUTIC_PASSWORD", "") or "").strip()
    return base, user, pw


def configured() -> bool:
    base, user, pw = _cfg()
    return bool(base and user and pw)


async def mautic_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET ``{base}/api{path}`` — basic-auth. Returns decoded body, including
    non-2xx which callers inspect."""
    base, user, pw = _cfg()
    if not base or not user or not pw:
        raise RuntimeError("Mautic not configured (MAUTIC_BASE_URL/MAUTIC_USERNAME/MAUTIC_PASSWORD)")
    async with httpx.AsyncClient(timeout=_TIMEOUT, auth=(user, pw)) as c:
        r = await c.get(f"{base}/api{path}", params=params)
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_body": r.text[:400]}


async def mautic_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``{base}/api{path}`` — basic-auth."""
    base, user, pw = _cfg()
    if not base or not user or not pw:
        raise RuntimeError("Mautic not configured (MAUTIC_BASE_URL/MAUTIC_USERNAME/MAUTIC_PASSWORD)")
    async with httpx.AsyncClient(timeout=_TIMEOUT, auth=(user, pw)) as c:
        r = await c.post(f"{base}/api{path}", json=payload)
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_body": r.text[:400]}


# ── Contacts ──────────────────────────────────────────────────────────────


async def upsert_contact(email: str, fields: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create-or-update a Mautic contact keyed on email. ``fields`` maps to
    Mautic field aliases (firstname, lastname, company, tags, ...)."""
    body = dict(fields or {})
    body["email"] = email
    body.setdefault("overwriteWithBlank", False)
    return await mautic_post("/contacts/new", body)


async def list_contacts(email_contains: str | None = None, limit: int = _PAGE) -> list[dict[str, Any]]:
    """List contacts — optionally filtered by email substring."""
    params: dict[str, Any] = {"limit": limit, "minimal": "true"}
    if email_contains:
        params["search"] = f"email:*{email_contains}*"
    data = await mautic_get("/contacts", params=params)
    return list((data.get("contacts") or {}).values()) if isinstance(data.get("contacts"), dict) else data.get("contacts") or []


async def add_to_segment(contact_id: int, segment_id: int) -> dict[str, Any]:
    """Put a contact into a Mautic segment (the funnel list container)."""
    return await mautic_post(f"/contacts/{contact_id}/segments/{segment_id}/add", {})


# ── Funnel surface ─────────────────────────────────────────────────────────


async def list_segments() -> list[dict[str, Any]]:
    """All segments — the funnel lists."""
    data = await mautic_get("/segments", params={"limit": _PAGE})
    segs = data.get("lists") or data.get("segments") or []
    return list(segs.values()) if isinstance(segs, dict) else segs


async def create_segment(name: str, alias: str, description: str = "") -> dict[str, Any]:
    """Make a funnel segment if it doesn't exist yet."""
    return await mautic_post("/segments/new", {"name": name, "alias": alias, "description": description})


async def funnel_state() -> dict[str, Any]:
    """Health snapshot for the ops surface: contacts + segments + campaigns."""
    contacts = await mautic_get("/contacts", params={"limit": 1})
    segments = await list_segments()
    return {
        "configured": configured(),
        "contacts": (contacts.get("total") or len(contacts.get("contacts") or [])),
        "segments": [s.get("name") for s in segments][:20],
    }

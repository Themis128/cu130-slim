"""Mautic funnel-management client — contacts, segments, campaigns.

Mautic runs in the docker stack (``mautic`` service) behind the
``authentik-outpost`` SSO gate on ``mautic.cloudless.gr``. The backend talks
to the container directly (``http://mautic:80``) — the tunnel's ``^/api/``
bypass means API calls never hit the outpost gate. Basic auth uses the admin
creds so every call is a real API user, not a session.

    MAUTIC_BASE_URL   e.g. http://mautic:80
    MAUTIC_USERNAME   admin user email (basic auth)
    MAUTIC_PASSWORD   admin user password
    MAUTIC_PUBLIC_HOST canonical host (default mautic.cloudless.gr) — sent as
                       Host + X-Forwarded-Proto so Mautic's canonical-URL
                       check passes for container-direct http calls.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 15.0
_PAGE = 50


def _cfg() -> tuple[str, str, str, str]:
    settings = get_settings()
    base = (getattr(settings, "MAUTIC_BASE_URL", "") or "").strip().rstrip("/")
    user = (getattr(settings, "MAUTIC_USERNAME", "") or "").strip()
    pw = (getattr(settings, "MAUTIC_PASSWORD", "") or "").strip()
    host = (getattr(settings, "MAUTIC_PUBLIC_HOST", "") or "").strip() or "mautic.cloudless.gr"
    return base, user, pw, host


def _headers() -> dict[str, str]:
    """Canonical-host headers — Mautic 301s/500s any API call whose Host or
    scheme doesn't match ``site_url`` (https://<public host>)."""
    *_, host = _cfg()
    return {"Host": host, "X-Forwarded-Proto": "https"}


def configured() -> bool:
    base, user, pw, _ = _cfg()
    return bool(base and user and pw)


async def mautic_get(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET ``{base}/api{path}`` — basic-auth. Returns decoded body, including
    non-2xx which callers inspect."""
    base, user, pw, _ = _cfg()
    if not base or not user or not pw:
        raise RuntimeError("Mautic not configured (MAUTIC_BASE_URL/MAUTIC_USERNAME/MAUTIC_PASSWORD)")
    async with httpx.AsyncClient(timeout=_TIMEOUT, auth=(user, pw), headers=_headers()) as c:
        r = await c.get(f"{base}/api{path}", params=params)
    try:
        return r.json()
    except Exception:
        return {"_status": r.status_code, "_body": r.text[:400]}


async def mautic_post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``{base}/api{path}`` — basic-auth, form-encoded (Mautic's API
    rejects JSON bodies on create routes)."""
    base, user, pw, _ = _cfg()
    if not base or not user or not pw:
        raise RuntimeError("Mautic not configured (MAUTIC_BASE_URL/MAUTIC_USERNAME/MAUTIC_PASSWORD)")
    flat: dict[str, Any] = {}
    for k, v in payload.items():
        flat[k] = ",".join(str(t) for t in v) if isinstance(v, list) else v
    async with httpx.AsyncClient(timeout=_TIMEOUT, auth=(user, pw), headers=_headers()) as c:
        r = await c.post(f"{base}/api{path}", data=flat)
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
    return await mautic_post(f"/segments/{segment_id}/contact/{contact_id}/add", {})


# ── Funnel surface ─────────────────────────────────────────────────────────


async def list_segments() -> list[dict[str, Any]]:
    """All segments — the funnel lists."""
    data = await mautic_get("/segments", params={"limit": _PAGE})
    segs = data.get("lists") or data.get("segments") or []
    return list(segs.values()) if isinstance(segs, dict) else segs


async def create_segment(name: str, alias: str, description: str = "") -> dict[str, Any]:
    """Make a funnel segment if it doesn't exist yet — idempotent: on an
    alias-in-use 400, returns the existing segment."""
    r = await mautic_post("/segments/new", {"name": name, "alias": alias, "description": description})
    if r.get("errors"):
        for s in await list_segments():
            if s.get("alias") == alias:
                return {"list": s, "id": s.get("id")}
    return r


async def funnel_state() -> dict[str, Any]:
    """Health snapshot for the ops surface: contacts + segments + campaigns."""
    contacts = await mautic_get("/contacts", params={"limit": 1})
    segments = await list_segments()
    return {
        "configured": configured(),
        "contacts": (contacts.get("total") or len(contacts.get("contacts") or [])),
        "segments": [s.get("name") for s in segments][:20],
    }

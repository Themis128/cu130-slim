"""LinkedIn organization access control — page admin/role management via API.

``rw_organization_admin`` (granted on app 264925843) allows managing
Company Page roles without the UI:

- ``list_acls`` — ``GET /rest/organizationAcls?q=organization`` finder.
- ``set_role`` — ``PUT /rest/organizationAcls/(organization:…,role:…,roleAssignee:…)``
  creates or updates a member's role (state REQUESTED = pending invite,
  APPROVED = active). Verified shape per MS Learn organization-access-control
  docs + community usage.
- ``remove_role`` — ``DELETE`` on the same key removes the assignment.

Valid roles (MS Learn): ADMINISTRATOR, DIRECT_SPONSORED_CONTENT_POSTER,
RECRUITING_POSTER, LEAD_CAPTURE_ADMINISTRATOR, LEAD_GEN_FORMS_MANAGER,
ANALYST, CURATOR, CONTENT_ADMINISTRATOR.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import quote

import httpx

from app.services import linkedin_feed

logger = logging.getLogger(__name__)

ROLES = {
    "ADMINISTRATOR",
    "DIRECT_SPONSORED_CONTENT_POSTER",
    "RECRUITING_POSTER",
    "LEAD_CAPTURE_ADMINISTRATOR",
    "LEAD_GEN_FORMS_MANAGER",
    "ANALYST",
    "CURATOR",
    "CONTENT_ADMINISTRATOR",
}

_STATES = {"REQUESTED", "APPROVED", "REJECTED", "REMOVED"}


def _key(org_urn: str, role: str, person_urn: str) -> str:
    return f"(organization:{quote(org_urn, safe='')},role:{role},roleAssignee:{quote(person_urn, safe='')})"


async def list_acls(role: str | None = None, state: str | None = None) -> dict[str, Any]:
    """List ACL entries for the org — ``GET /rest/organizationAcls``."""
    ctx = await linkedin_feed.org_ctx()
    if not ctx:
        return {"acls": [], "error": "no-linkedin-account"}
    token, org_urn = ctx
    params: dict[str, str] = {"q": "organization", "organization": org_urn, "count": 100}
    if role:
        params["role"] = role
    if state:
        params["state"] = state
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.get(
            f"{linkedin_feed.REST}/organizationAcls",
            params=params,
            headers={"Authorization": f"Bearer {token}", **linkedin_feed.HDRS},
        )
    if r.status_code != 200:
        return {"acls": [], "status": r.status_code, "error": r.text[:200]}
    return {"acls": r.json().get("elements") or [], "status": 200}


async def set_role(person_urn: str, role: str = "ADMINISTRATOR", state: str = "REQUESTED") -> dict[str, Any]:
    """Assign (or re-assign) an org role to a member.

    ``person_urn`` is ``urn:li:person:{id}`` or a member id. ``state``
    REQUESTED sends the page-admin invite; APPROVED only works when the
    member already accepted.
    """
    if role not in ROLES:
        return {"error": f"bad role {role!r}", "allowed": sorted(ROLES)}
    if state not in _STATES:
        return {"error": f"bad state {state!r}", "allowed": sorted(_STATES)}
    if not person_urn.startswith("urn:li:person:") and not person_urn.isdigit():
        person_urn = f"urn:li:person:{person_urn}"
    ctx = await linkedin_feed.org_ctx()
    if not ctx:
        return {"error": "no-linkedin-account"}
    token, org_urn = ctx
    key = _key(org_urn, role, person_urn)
    payload = {
        "organization": org_urn,
        "role": role,
        "roleAssignee": person_urn,
        "state": state,
    }
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.put(
            f"{linkedin_feed.REST}/organizationAcls/{key}",
            json=payload,
            headers={"Authorization": f"Bearer {token}", **linkedin_feed.HDRS, "Content-Type": "application/json"},
        )
    out: dict[str, Any] = {"status": r.status_code, "ok": r.status_code in (200, 201, 204)}
    if not out["ok"]:
        out["error"] = r.text[:300]
    return out


async def remove_role(person_urn: str, role: str = "ADMINISTRATOR") -> dict[str, Any]:
    """Remove a member's org role — ``DELETE /rest/organizationAcls/{key}``."""
    if role not in ROLES:
        return {"error": f"bad role {role!r}", "allowed": sorted(ROLES)}
    if not person_urn.startswith("urn:li:person:") and not person_urn.isdigit():
        person_urn = f"urn:li:person:{person_urn}"
    ctx = await linkedin_feed.org_ctx()
    if not ctx:
        return {"error": "no-linkedin-account"}
    token, org_urn = ctx
    key = _key(org_urn, role, person_urn)
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.delete(
            f"{linkedin_feed.REST}/organizationAcls/{key}",
            headers={"Authorization": f"Bearer {token}", **linkedin_feed.HDRS},
        )
    out: dict[str, Any] = {"status": r.status_code, "ok": r.status_code in (200, 204)}
    if not out["ok"]:
        out["error"] = r.text[:300]
    return out

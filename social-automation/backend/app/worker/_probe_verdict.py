"""Tri-state probe verdicts for account/session health checks.

Contract (established 2026-11-22 after the IG mass-expire incident):

- ``True``  — the platform definitively answered healthy (2xx).
- ``False`` — the platform definitively rejected (4xx auth/not-found).
- ``None``  — inconclusive: transport error, 5xx, or unexpected status.
  Callers MUST treat None as "cannot determine" and leave account
  status untouched — never flip to expired/revoked on it.

A single transport blip (worker DNS failure, sidecar restart) must never
mass-expire live accounts: publish lanes filter on ``status='active'``
and a false flip silently darkens the channel.
"""

from __future__ import annotations

# HTTP statuses that definitively mean "credential/session rejected".
# 404 is deliberately excluded — a missing route is an infra problem,
# not proof the credential died.
_REJECT_STATUSES = frozenset({400, 401, 403})


def verdict_from_status(status_code: int) -> bool | None:
    """Map an HTTP status to True (healthy) / False (rejected) / None."""
    if 200 <= status_code < 300:
        return True
    if status_code in _REJECT_STATUSES:
        return False
    return None


def verdict_from_response(resp) -> bool | None:
    """``verdict_from_status`` for an httpx-style response object."""
    return verdict_from_status(resp.status_code)

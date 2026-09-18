"""Polar.sh API client (v1) — Merchant of Record billing backend.

Thin async wrapper over ``https://(sandbox-)api.polar.sh/v1`` used by the
billing router: checkouts (hosted checkout sessions), subscriptions, customer
portal sessions, and Standard Webhooks signature verification.

Docs: https://polar.sh/docs/api-reference
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time
from datetime import UTC, datetime

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Webhook tolerance for replay protection (Standard Webhooks recommends 5 min).
WEBHOOK_TOLERANCE_S = 300


class PolarError(Exception):
    """Raised when the Polar API returns an error response."""


def _settings():
    return get_settings()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_settings().POLAR_ACCESS_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def polar_configured() -> bool:
    s = _settings()
    return bool(s.POLAR_ACCESS_TOKEN and s.polar_product_tiers)


async def _request(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated Polar API call; return the JSON payload."""
    s = _settings()
    url = f"{s.polar_api_base}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=_headers(), **kwargs)
    try:
        body = resp.json()
    except Exception:
        body = {}
    if resp.status_code >= 400:
        detail = body.get("detail") or resp.text[:300]
        if isinstance(detail, list):
            detail = "; ".join(str(d.get("msg", d)) for d in detail)[:300]
        raise PolarError(f"Polar {method} {path} -> {resp.status_code}: {detail}")
    return body


def verify_webhook_signature(
    raw_body: bytes,
    webhook_id: str,
    webhook_timestamp: str,
    webhook_signature: str,
) -> bool:
    """Verify a Polar webhook per the Standard Webhooks spec.

    Polar secrets (``polar_whs_…``/``whsec_…``) are base64-encoded HMAC keys;
    the signed payload is ``f"{id}.{timestamp}.{body}"``. The
    ``webhook-signature`` header is a space-separated list of
    ``v1,<base64-sig>`` entries. Timestamp freshness blocks replay.
    """
    secret = _settings().POLAR_WEBHOOK_SECRET
    if not secret or not (webhook_id and webhook_timestamp and webhook_signature):
        return False
    try:
        ts = int(webhook_timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > WEBHOOK_TOLERANCE_S:
        return False

    # Strip the Standard Webhooks prefix (whsec_ / polar_whs_); remainder is
    # base64 key material. Fall back to raw bytes if it doesn't decode.
    key_material = secret
    for prefix in ("polar_whs_", "whsec_"):
        if key_material.startswith(prefix):
            key_material = key_material[len(prefix):]
            break
    try:
        key = base64.b64decode(key_material)
    except Exception:
        key = secret.encode()

    signed = f"{webhook_id}.{webhook_timestamp}.".encode() + raw_body
    expected = base64.b64encode(
        hmac.new(key, signed, hashlib.sha256).digest()
    ).decode()

    for part in webhook_signature.split(" "):
        scheme, _, sig = part.partition(",")
        if scheme == "v1" and hmac.compare_digest(sig, expected):
            return True
    return False


async def get_or_create_customer(team_id: str, email: str, name: str | None = None) -> str:
    """Return the Polar customer ID for a team, keyed by ``external_id``."""
    try:
        cust = await _request("GET", f"/customers/external/{team_id}")
        return cust["id"]
    except PolarError as exc:
        if "404" not in str(exc):
            raise
    payload: dict = {
        "email": email,
        "external_id": team_id,
        "type": "individual",
        "metadata": {"team_id": team_id},
    }
    if name:
        payload["name"] = name
    created = await _request("POST", "/customers/", json=payload)
    return created["id"]


async def create_checkout(
    *,
    product_id: str,
    team_id: str,
    customer_id: str | None = None,
    customer_email: str | None = None,
    customer_name: str | None = None,
    discount_id: str | None = None,
) -> dict:
    """Create a hosted checkout → returns ``{id, url}``.

    ``metadata.team_id`` and ``external_customer_id`` flow through to webhook
    events so the team resolves without extra lookups. ``discount_id``
    pre-applies a Polar discount on the hosted checkout.
    """
    s = _settings()
    payload: dict = {
        "products": [product_id],
        "metadata": {"team_id": team_id},
        "success_url": f"{s.FRONTEND_URL.rstrip('/')}/settings/billing?checkout=success",
        "return_url": f"{s.FRONTEND_URL.rstrip('/')}/settings/billing",
    }
    if customer_id:
        payload["customer_id"] = customer_id
    else:
        payload["external_customer_id"] = team_id
    if customer_email:
        payload["customer_email"] = customer_email
    if customer_name:
        payload["customer_name"] = customer_name
    if discount_id:
        payload["discount_id"] = discount_id
    data = await _request("POST", "/checkouts/", json=payload)
    return {"id": data["id"], "checkout_url": data.get("url")}


async def get_discount_for_code(code: str) -> dict | None:
    """Return the Polar discount whose ``code`` matches (case-insensitive).

    ``GET /discounts`` is filtered server-side by ``query`` (matches name or
    code); we re-match the code client-side per Polar docs. Returns the raw
    discount dict regardless of redemption state — callers use
    ``discount_is_redeemable`` to decide whether it can be applied now.
    """
    code = (code or "").strip()
    if not code:
        return None
    data = await _request("GET", "/discounts/", params={"query": code, "limit": 100})
    for d in data.get("items") or []:
        if (d.get("code") or "").lower() == code.lower():
            return d
    return None


def discount_is_redeemable(discount: dict, now: datetime | None = None) -> bool:
    """True when the discount is inside its window and under its cap."""
    now = now or datetime.now(UTC)
    starts_at = _parse_polar_dt(discount.get("starts_at"))
    ends_at = _parse_polar_dt(discount.get("ends_at"))
    if starts_at and starts_at > now:
        return False
    if ends_at and ends_at < now:
        return False
    max_red = discount.get("max_redemptions")
    if max_red is not None and (discount.get("redemptions_count") or 0) >= max_red:
        return False
    return True


async def get_discount_id_for_code(code: str) -> str | None:
    """Resolve a redeemable Polar discount code to its discount ID.

    Returns ``None`` when the code can't be resolved or isn't currently
    redeemable — the caller treats that as "no discount" and still lets the
    customer type the code manually on the hosted checkout page.
    """
    d = await get_discount_for_code(code)
    if d and discount_is_redeemable(d):
        return d.get("id")
    return None


def _parse_polar_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def get_subscription(subscription_id: str) -> dict:
    return await _request("GET", f"/subscriptions/{subscription_id}")


async def cancel_subscription(subscription_id: str) -> dict:
    """Schedule cancellation at period end."""
    return await _request(
        "PATCH",
        f"/subscriptions/{subscription_id}",
        json={"cancel_at_period_end": True},
    )


async def create_portal_session(customer_id: str) -> str:
    """Return the Polar hosted customer-portal URL for this customer.

    Uses ``customer_id`` (not ``external_customer_id``): Polar merges
    customers by email, so a customer may be bound to a different team's
    external_id than the one requesting the portal.
    """
    s = _settings()
    data = await _request(
        "POST",
        "/customer-sessions/",
        json={
            "customer_id": customer_id,
            "return_url": f"{s.FRONTEND_URL.rstrip('/')}/settings/billing",
        },
    )
    return data["customer_portal_url"]


def tier_for_product(product_id: str) -> str | None:
    """Map a Polar product_id to a SocialAuto plan tier via env mapping."""
    return _settings().polar_product_tiers.get(product_id)

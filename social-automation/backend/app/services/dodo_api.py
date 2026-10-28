"""Dodo Payments API client — Merchant of Record billing backend.

Thin async wrapper over ``https://(test|live).dodopayments.com`` used by the
billing router: hosted checkout sessions, customers, customer-portal sessions,
subscriptions, and Standard Webhooks signature verification (same spec Polar
uses: ``webhook-id``/``webhook-timestamp``/``webhook-signature`` headers,
``{id}.{ts}.{body}`` HMAC-SHA256, base64 ``whsec_``-style secrets).

Docs: https://docs.dodopayments.com/api-reference
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import time

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

# Standard Webhooks replay-protection tolerance.
WEBHOOK_TOLERANCE_S = 300


class DodoError(Exception):
    """Raised when the Dodo Payments API returns an error response."""


def _settings():
    return get_settings()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_settings().DODO_PAYMENTS_API_KEY}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def dodo_configured() -> bool:
    s = _settings()
    return bool(s.DODO_PAYMENTS_API_KEY and s.dodo_product_tiers)


async def _request(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated Dodo API call; return the JSON payload."""
    s = _settings()
    url = f"{s.dodo_api_base}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=_headers(), **kwargs)
    try:
        body = resp.json()
    except Exception:
        body = {}
    if resp.status_code >= 400:
        detail = body.get("detail") or body.get("message") or resp.text[:300]
        if isinstance(detail, list):
            detail = "; ".join(str(d.get("msg", d)) for d in detail)[:300]
        raise DodoError(f"Dodo {method} {path} -> {resp.status_code}: {detail}")
    return body


def verify_webhook_signature(
    raw_body: bytes,
    webhook_id: str,
    webhook_timestamp: str,
    webhook_signature: str,
) -> bool:
    """Verify a Dodo webhook per the Standard Webhooks spec."""
    secret = _settings().DODO_WEBHOOK_SECRET
    if not secret or not (webhook_id and webhook_timestamp and webhook_signature):
        return False
    try:
        ts = int(webhook_timestamp)
    except (TypeError, ValueError):
        return False
    if abs(time.time() - ts) > WEBHOOK_TOLERANCE_S:
        return False

    key_material = secret
    for prefix in ("whsec_", "dodo_whs_"):
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
    """Return the Dodo customer ID for a team's owner email."""
    customers = await _request("GET", "/customers", params={"email": email, "page_size": "10"})
    items = customers.get("items") or customers.get("data") or []
    for c in items:
        if c.get("email", "").lower() == email.lower():
            return c["customer_id"]
    payload: dict = {
        "email": email,
        "metadata": {"team_id": team_id},
    }
    if name:
        payload["name"] = name
    created = await _request("POST", "/customers", json=payload)
    return created["customer_id"]


async def create_checkout(
    *,
    product_id: str,
    team_id: str,
    customer_id: str | None = None,
    customer_email: str | None = None,
    customer_name: str | None = None,
) -> dict:
    """Create a hosted checkout session → returns ``{id, checkout_url}``.

    ``metadata.team_id`` flows through to payment/subscription webhook events
    so the team resolves without extra lookups.
    """
    s = _settings()
    payload: dict = {
        "product_cart": [{"product_id": product_id, "quantity": 1}],
        "metadata": {"team_id": team_id},
        "return_url": f"{s.FRONTEND_URL.rstrip('/')}/settings/billing?checkout=success",
    }
    if customer_id:
        payload["customer"] = {"customer_id": customer_id}
    else:
        payload["customer"] = {"email": customer_email or "", "name": customer_name or ""}
    data = await _request("POST", "/checkouts", json=payload)
    return {"id": data.get("session_id", ""), "checkout_url": data.get("checkout_url")}


async def get_subscription(subscription_id: str) -> dict:
    return await _request("GET", f"/subscriptions/{subscription_id}")


async def cancel_subscription(subscription_id: str) -> dict:
    """Schedule cancellation at the next billing date."""
    return await _request(
        "PATCH",
        f"/subscriptions/{subscription_id}",
        json={"cancel_at_next_billing_date": True},
    )


async def create_portal_session(customer_id: str) -> str:
    """Return the Dodo hosted customer-portal link for this customer."""
    data = await _request(
        "POST",
        f"/customers/{customer_id}/customer-portal/session",
        params={"send_email": "false"},
    )
    return data["link"]


async def live_payments_enabled() -> bool:
    """Probe whether the merchant is approved for live payments.

    Dodo checks the ``MERCHANT_NOT_LIVE`` gate before validating the request
    body, so a deliberately invalid product id is enough — no real checkout
    is ever created. Returns True once the gate lifts (any response other
    than the not-live error, including validation 422s).
    """
    try:
        await _request(
            "POST",
            "/checkouts",
            json={"product_cart": [{"product_id": "live-check-probe", "quantity": 1}]},
        )
        return True
    except DodoError as exc:
        if "live payments not enabled" in str(exc).lower():
            return False
        # Any other error (validation, bad product, ...) means the gate lifted.
        return True


def tier_for_product(product_id: str) -> str | None:
    """Map a Dodo product_id to a SocialAuto plan tier via env mapping."""
    return _settings().dodo_product_tiers.get(product_id)

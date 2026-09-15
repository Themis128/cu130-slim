"""Paddle Billing API client (v2).

Thin async wrapper over ``https://(sandbox-)api.paddle.com`` used by the
billing router: transactions (checkout), subscriptions, customer portal
sessions, and webhook signature verification.

Docs: https://developer.paddle.com/api-reference/overview
"""
from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class PaddleError(Exception):
    """Raised when the Paddle API returns an error response."""


def _settings():
    return get_settings()


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_settings().PADDLE_API_KEY}",
        "Content-Type": "application/json",
    }


def paddle_configured() -> bool:
    s = _settings()
    return bool(s.PADDLE_API_KEY and s.PADDLE_CLIENT_TOKEN)


async def _request(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated Paddle API call; return ``data`` payload."""
    s = _settings()
    url = f"{s.paddle_api_base}{path}"
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.request(method, url, headers=_headers(), **kwargs)
    try:
        body = resp.json()
    except Exception:
        body = {}
    if resp.status_code >= 400:
        detail = (body.get("error") or {}).get("detail") or resp.text[:300]
        raise PaddleError(f"Paddle {method} {path} -> {resp.status_code}: {detail}")
    return body


def verify_webhook_signature(raw_body: bytes, signature_header: str) -> bool:
    """Verify ``Paddle-Signature`` header: ``ts=...,h1=...``.

    Signed payload is ``f"{ts}:{raw_body}"`` hashed with the notification
    secret via HMAC-SHA256. Constant-time compare; ts freshness (5 min) is
    enforced to block replay.
    """
    secret = _settings().PADDLE_WEBHOOK_SECRET
    if not secret or not signature_header:
        return False
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(";"))
        ts, h1 = parts["ts"], parts["h1"]
    except Exception:
        return False
    import time

    if abs(time.time() - int(ts)) > 300:
        return False
    digest = hmac.new(secret.encode(), f"{ts}:".encode() + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(digest, h1)


async def get_or_create_customer(team_id: str, email: str, name: str | None = None) -> str:
    """Return the Paddle customer ID for a team, creating one if needed."""
    # Paddle customers are looked up by email; custom_data links back to us.
    search = await _request("GET", "/customers", params={"email": email})
    for cust in search.get("data", []):
        if (cust.get("custom_data") or {}).get("team_id") == team_id:
            return cust["id"]
    payload: dict = {"email": email, "custom_data": {"team_id": team_id}}
    if name:
        payload["name"] = name
    created = await _request("POST", "/customers", json=payload)
    return created["data"]["id"]


async def create_checkout_transaction(
    *,
    price_id: str,
    team_id: str,
    customer_id: str | None = None,
    customer_email: str | None = None,
) -> dict:
    """Create a transaction → returns ``{id, checkout_url}``.

    ``custom_data.team_id`` flows through to webhook events so the team can
    be resolved without an extra lookup.
    """
    s = _settings()
    payload: dict = {
        "items": [{"price_id": price_id, "quantity": 1}],
        "custom_data": {"team_id": team_id},
        "checkout": {"url": f"{s.FRONTEND_URL.rstrip('/')}/settings/billing?checkout=success"},
    }
    if customer_id:
        payload["customer_id"] = customer_id
    elif customer_email:
        payload["customer"] = {"email": customer_email}
    data = await _request("POST", "/transactions", json=payload)
    txn = data["data"]
    return {"id": txn["id"], "checkout_url": (txn.get("checkout") or {}).get("url")}


async def get_subscription(subscription_id: str) -> dict:
    data = await _request("GET", f"/subscriptions/{subscription_id}")
    return data["data"]


async def cancel_subscription(subscription_id: str) -> dict:
    """Schedule cancellation at period end (effective_from=next_billing_period)."""
    data = await _request(
        "POST",
        f"/subscriptions/{subscription_id}/cancel",
        json={"effective_from": "next_billing_period"},
    )
    return data["data"]


async def create_portal_session(customer_id: str, subscription_id: str | None = None) -> str:
    """Return the Paddle hosted customer-portal URL for this customer."""
    payload: dict = {}
    if subscription_id:
        payload["subscription_ids"] = [subscription_id]
    data = await _request("POST", f"/customers/{customer_id}/portal-sessions", json=payload)
    urls = (data.get("data") or {}).get("urls") or {}
    return (urls.get("general") or {}).get("overview") or urls.get("subscriptions", [{}])[0].get(
        "update_subscription_payment_method", ""
    )


def tier_for_price(price_id: str) -> str | None:
    """Map a Paddle price_id to a SocialAuto plan tier via env mapping."""
    return _settings().paddle_price_tiers.get(price_id)

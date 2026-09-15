"""Website analytics forwarding: ingest from owned sites and push to GA4, Plausible, Meta."""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from app.core.config import get_settings
from app.models.web_analytics import WebAnalyticsConfig, WebAnalyticsEvent

logger = logging.getLogger(__name__)
settings = get_settings()

FORWARD_TIMEOUT = 10.0


def _hash(value: str | None) -> str | None:
    if not value:
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode()).hexdigest()


def _event_time_ms(event: WebAnalyticsEvent) -> int:
    dt = event.occurred_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1000)


def _client_info(event: WebAnalyticsEvent) -> dict[str, Any]:
    info: dict[str, Any] = {}
    if event.client_ip:
        info["client_ip_address"] = event.client_ip
    if event.user_agent:
        info["client_user_agent"] = event.user_agent
    return info


async def forward_to_ga4(
    client: httpx.AsyncClient,
    event: WebAnalyticsEvent,
    config: WebAnalyticsConfig,
) -> dict[str, Any]:
    """Send event to GA4 Measurement Protocol."""
    if not config.ga4_enabled or not config.ga4_measurement_id or not config.ga4_api_secret:
        return {"ok": False, "skipped": True, "reason": "not_configured"}

    params = {"api_secret": config.ga4_api_secret, "measurement_id": config.ga4_measurement_id}
    payload: dict[str, Any] = {
        "client_id": event.visitor_id or event.session_id or str(event.id),
        "events": [
            {
                "name": event.event_name,
                "params": {
                    "event_id": str(event.id),
                    "page_location": f"https://{event.domain}{event.path or '/'}",
                    "page_referrer": event.referrer or "",
                    **event.payload,
                },
            }
        ],
    }
    if event.locale:
        payload["user_language"] = event.locale

    try:
        resp = await client.post(
            "https://www.google-analytics.com/mp/collect",
            params=params,
            json=payload,
            timeout=FORWARD_TIMEOUT,
        )
        if resp.status_code >= 400:
            return {"ok": False, "status": resp.status_code, "body": resp.text[:200]}
        return {"ok": True, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        logger.warning("GA4 forward failed for event %s: %s", event.id, exc)
        return {"ok": False, "error": str(exc)}


async def forward_to_plausible(
    client: httpx.AsyncClient,
    event: WebAnalyticsEvent,
    config: WebAnalyticsConfig,
) -> dict[str, Any]:
    """Send event to Plausible Events API."""
    if not config.plausible_enabled or not config.plausible_domain:
        return {"ok": False, "skipped": True, "reason": "not_configured"}

    url = config.plausible_api_url or "https://plausible.io/api/event"
    headers: dict[str, str] = {"User-Agent": event.user_agent or "SocialAuto/1.0"}
    if config.plausible_api_key:
        headers["Authorization"] = f"Bearer {config.plausible_api_key}"

    payload = {
        "name": event.event_name,
        "url": f"https://{event.domain}{event.path or '/'}",
        "domain": config.plausible_domain,
        "props": {"event_id": str(event.id), **event.payload},
    }

    try:
        resp = await client.post(url, json=payload, headers=headers, timeout=FORWARD_TIMEOUT)
        if resp.status_code >= 400:
            return {"ok": False, "status": resp.status_code, "body": resp.text[:200]}
        return {"ok": True, "status": resp.status_code}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Plausible forward failed for event %s: %s", event.id, exc)
        return {"ok": False, "error": str(exc)}


async def forward_to_meta_capi(
    client: httpx.AsyncClient,
    event: WebAnalyticsEvent,
    config: WebAnalyticsConfig,
) -> dict[str, Any]:
    """Send event to Meta Conversions API.

    Maps homepage events to Meta standard events where appropriate:
    - home_newsletter_subscribe / contact_submit → Lead
    - checkout_start → InitiateCheckout
    - checkout_complete → Purchase
    Everything else is sent as a custom event.
    """
    if not config.meta_capi_enabled or not config.meta_pixel_id or not config.meta_capi_access_token:
        return {"ok": False, "skipped": True, "reason": "not_configured"}

    event_name_mapping = {
        "home_newsletter_subscribe": "Lead",
        "newsletter_signup": "Lead",
        "contact_submit": "Lead",
        "home_inline_audit_submit": "Lead",
        "checkout_start": "InitiateCheckout",
        "checkout_complete": "Purchase",
    }
    meta_name = event_name_mapping.get(event.event_name, event.event_name)

    user_data = _client_info(event)
    email = event.payload.get("email") if isinstance(event.payload, dict) else None
    phone = event.payload.get("phone") if isinstance(event.payload, dict) else None
    if email:
        user_data["em"] = [_hash(str(email))]
    if phone:
        user_data["ph"] = [_hash(str(phone))]

    payload = {
        "data": [
            {
                "event_name": meta_name,
                "event_time": int(event.occurred_at.timestamp()) if event.occurred_at else int(
                    datetime.now(UTC).timestamp()
                ),
                "event_id": str(event.id),
                "event_source_url": f"https://{event.domain}{event.path or '/'}",
                "action_source": "website",
                "user_data": user_data,
                "custom_data": {"event_id": str(event.id), **event.payload},
            }
        ],
        "access_token": config.meta_capi_access_token,
    }

    url = f"https://graph.facebook.com/v18.0/{config.meta_pixel_id}/events"
    try:
        resp = await client.post(url, json=payload, timeout=FORWARD_TIMEOUT)
        if resp.status_code >= 400:
            return {"ok": False, "status": resp.status_code, "body": resp.text[:200]}
        return {"ok": True, "status": resp.status_code, "body": resp.text[:200]}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Meta CAPI forward failed for event %s: %s", event.id, exc)
        return {"ok": False, "error": str(exc)}


async def forward_event(event: WebAnalyticsEvent, config: WebAnalyticsConfig) -> dict[str, Any]:
    """Forward a single event to all configured sinks concurrently."""
    async with httpx.AsyncClient() as client:
        ga4_task = forward_to_ga4(client, event, config)
        plausible_task = forward_to_plausible(client, event, config)
        meta_task = forward_to_meta_capi(client, event, config)
        ga4, plausible, meta = await ga4_task, await plausible_task, await meta_task
    return {"ga4": ga4, "plausible": plausible, "meta": meta}


def _derive_event_time(payload: dict[str, Any]) -> datetime:
    ts = payload.get("timestamp") or payload.get("event_time")
    if isinstance(ts, (int, float)):
        try:
            return datetime.fromtimestamp(ts / 1000 if ts > 1e10 else ts, tz=UTC)
        except (OSError, OverflowError, ValueError):
            pass
    if isinstance(ts, str):
        try:
            return datetime.fromisoformat(ts).replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.now(UTC)


async def ingest_event(
    *,
    team_id: uuid.UUID,
    config: WebAnalyticsConfig,
    domain: str,
    event_name: str,
    payload: dict[str, Any],
    session_id: str | None = None,
    visitor_id: str | None = None,
    path: str | None = None,
    referrer: str | None = None,
    user_agent: str | None = None,
    client_ip: str | None = None,
    locale: str | None = None,
) -> WebAnalyticsEvent:
    """Persist a website event and forward it to configured sinks."""
    event = WebAnalyticsEvent(
        id=uuid.uuid4(),
        team_id=team_id,
        config_id=config.id,
        domain=domain,
        event_name=event_name,
        session_id=session_id,
        visitor_id=visitor_id,
        path=path,
        referrer=referrer,
        user_agent=user_agent,
        client_ip=client_ip,
        locale=locale,
        payload=payload,
        occurred_at=_derive_event_time(payload),
    )

    # Forward before commit so we can store results; failures are non-fatal.
    try:
        event.forwarded = await forward_event(event, config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected error forwarding web analytics event %s", event.id)
        event.forwarded = {"error": str(exc)}

    return event

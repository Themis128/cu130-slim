"""Unit tests for the website analytics ingestion and forwarding service."""
from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, datetime

import pytest

from app.models.web_analytics import WebAnalyticsConfig, WebAnalyticsEvent
from app.services.web_analytics import forward_event, ingest_event


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "ok"):
        self.status_code = status_code
        self.text = text

    def json(self):
        return json.loads(self.text)


class FakeHttpxClient:
    def __init__(self, responses: dict[str, FakeResponse] | None = None):
        self.responses = responses or {}
        self.requests: list[tuple[str, str, dict, dict]] = []

    async def post(self, url: str, *, json: dict | None = None, headers: dict | None = None, params: dict | None = None, **kwargs):
        self.requests.append(("POST", url, headers or {}, json or {}, params or {}))
        return self.responses.get(url, FakeResponse())

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


@pytest.fixture
def config() -> WebAnalyticsConfig:
    return WebAnalyticsConfig(
        id=uuid.uuid4(),
        team_id=uuid.uuid4(),
        domain="cloudless.gr",
        webhook_secret="secret",
        ga4_enabled=True,
        ga4_measurement_id="G-TEST",
        ga4_api_secret="api-secret",
        plausible_enabled=True,
        plausible_domain="cloudless.gr",
        plausible_api_url="https://plausible.io/api/event",
        plausible_api_key="plausible-key",
        meta_capi_enabled=True,
        meta_pixel_id="1234567890",
        meta_capi_access_token="meta-token",
    )


@pytest.fixture
def event(config: WebAnalyticsConfig) -> WebAnalyticsEvent:
    return WebAnalyticsEvent(
        id=uuid.uuid4(),
        team_id=config.team_id,
        config_id=config.id,
        domain="cloudless.gr",
        event_name="home_hero_audit_click",
        path="/",
        session_id="s-1",
        visitor_id="v-1",
        payload={"cta": "audit"},
        occurred_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_forward_event_sends_to_all_sinks(config: WebAnalyticsConfig, event: WebAnalyticsEvent):
    responses = {
        "https://www.google-analytics.com/mp/collect": FakeResponse(),
        "https://plausible.io/api/event": FakeResponse(),
        "https://graph.facebook.com/v18.0/1234567890/events": FakeResponse(),
    }
    fake = FakeHttpxClient(responses)

    # Patch httpx.AsyncClient in the service module
    import app.services.web_analytics as wa_module

    original = wa_module.httpx.AsyncClient
    wa_module.httpx.AsyncClient = lambda **kwargs: fake  # type: ignore[assignment]
    try:
        result = await forward_event(event, config)
    finally:
        wa_module.httpx.AsyncClient = original  # type: ignore[misc]

    assert result["ga4"]["ok"] is True
    assert result["plausible"]["ok"] is True
    assert result["meta"]["ok"] is True
    assert len(fake.requests) == 3

    ga4_url, ga4_params = fake.requests[0][1], fake.requests[0][4]
    assert ga4_params["api_secret"] == "api-secret"
    assert ga4_params["measurement_id"] == "G-TEST"

    plausible_body = fake.requests[1][3]
    assert plausible_body["name"] == "home_hero_audit_click"
    assert plausible_body["domain"] == "cloudless.gr"

    meta_body = fake.requests[2][3]
    assert meta_body["data"][0]["event_name"] == "home_hero_audit_click"


@pytest.mark.asyncio
async def test_forward_event_skips_unconfigured_sinks(config: WebAnalyticsConfig, event: WebAnalyticsEvent):
    config.ga4_enabled = False
    config.plausible_enabled = False
    config.meta_capi_enabled = False
    fake = FakeHttpxClient({})

    import app.services.web_analytics as wa_module

    original = wa_module.httpx.AsyncClient
    wa_module.httpx.AsyncClient = lambda **kwargs: fake  # type: ignore[assignment]
    try:
        result = await forward_event(event, config)
    finally:
        wa_module.httpx.AsyncClient = original  # type: ignore[misc]

    assert result["ga4"]["skipped"] is True
    assert result["plausible"]["skipped"] is True
    assert result["meta"]["skipped"] is True
    assert fake.requests == []


@pytest.mark.asyncio
async def test_ingest_event_stores_forwarded_results(config: WebAnalyticsConfig):
    fake = FakeHttpxClient(
        {
            "https://www.google-analytics.com/mp/collect": FakeResponse(),
            "https://plausible.io/api/event": FakeResponse(),
            "https://graph.facebook.com/v18.0/1234567890/events": FakeResponse(),
        }
    )

    import app.services.web_analytics as wa_module

    original = wa_module.httpx.AsyncClient
    wa_module.httpx.AsyncClient = lambda **kwargs: fake  # type: ignore[assignment]
    try:
        event = await ingest_event(
            team_id=config.team_id,
            config=config,
            domain="cloudless.gr",
            event_name="home_newsletter_subscribe",
            payload={"source": "homepage"},
        )
    finally:
        wa_module.httpx.AsyncClient = original  # type: ignore[misc]

    assert event.event_name == "home_newsletter_subscribe"
    assert event.team_id == config.team_id
    assert event.forwarded["ga4"]["ok"] is True
    assert event.forwarded["plausible"]["ok"] is True
    assert event.forwarded["meta"]["ok"] is True


@pytest.mark.asyncio
async def test_ingest_event_timestamp_from_unix_ms(config: WebAnalyticsConfig):
    fake = FakeHttpxClient({})
    import app.services.web_analytics as wa_module

    original = wa_module.httpx.AsyncClient
    wa_module.httpx.AsyncClient = lambda **kwargs: fake  # type: ignore[assignment]
    try:
        event = await ingest_event(
            team_id=config.team_id,
            config=config,
            domain="cloudless.gr",
            event_name="test",
            payload={"timestamp": 1_700_000_000_000},  # ms
        )
    finally:
        wa_module.httpx.AsyncClient = original  # type: ignore[misc]

    assert event.occurred_at is not None
    assert event.occurred_at.year == 2023


def test_web_event_signature_verification():
    """HMAC helper produces the same value as the sender."""
    secret = "secret"
    payload = '{"event":"test"}'
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    from app.api.web_analytics import _verify_webhook_signature

    assert _verify_webhook_signature(payload.encode(), secret, expected) is True
    assert _verify_webhook_signature(payload.encode(), secret, "bad") is False

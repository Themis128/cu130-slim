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

    ga4_params = fake.requests[0][4]
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


# ── helpers + uncovered branches ─────────────────────────────────────


def test_hash_normalizes_and_none():
    from app.services.web_analytics import _hash

    assert _hash(None) is None
    assert _hash("") is None and _hash("   ") is None
    assert _hash("  A@B.COM ") == hashlib.sha256(b"a@b.com").hexdigest()


def test_event_time_ms_naive_and_aware(event: WebAnalyticsEvent):
    from app.services.web_analytics import _event_time_ms

    aware = _event_time_ms(event)
    event.occurred_at = event.occurred_at.replace(tzinfo=None)
    assert _event_time_ms(event) == aware


def test_client_info(event: WebAnalyticsEvent):
    from app.services.web_analytics import _client_info

    assert _client_info(event) == {}
    event.client_ip = "1.2.3.4"
    event.user_agent = "UA"
    assert _client_info(event) == {"client_ip_address": "1.2.3.4", "client_user_agent": "UA"}


@pytest.mark.asyncio
async def test_ga4_partial_config_and_error(config: WebAnalyticsConfig, event: WebAnalyticsEvent):
    from app.services.web_analytics import forward_to_ga4

    config.ga4_api_secret = ""  # enabled but missing secret → skip
    out = await forward_to_ga4(FakeHttpxClient({}), event, config)
    assert out["skipped"] is True
    config.ga4_api_secret = "s"

    err_url = "https://www.google-analytics.com/mp/collect"
    out = await forward_to_ga4(FakeHttpxClient({err_url: FakeResponse(400, "bad")}), event, config)
    assert out["ok"] is False and out["status"] == 400

    class _Raise:
        async def post(self, *a, **kw):
            raise RuntimeError("net down")

    out = await forward_to_ga4(_Raise(), event, config)
    assert out["ok"] is False and "net down" in out["error"]


@pytest.mark.asyncio
async def test_ga4_locale_and_client_id_fallback(config: WebAnalyticsConfig, event: WebAnalyticsEvent):
    from app.services.web_analytics import forward_to_ga4

    event.locale = "el"
    event.visitor_id = None  # falls back to session_id
    fake = FakeHttpxClient({})
    out = await forward_to_ga4(fake, event, config)
    assert out["ok"] is True
    body = fake.requests[0][3]
    assert body["user_language"] == "el"
    assert body["client_id"] == "s-1"


@pytest.mark.asyncio
async def test_plausible_paths(config: WebAnalyticsConfig, event: WebAnalyticsEvent):
    from app.services.web_analytics import forward_to_plausible

    config.plausible_domain = ""
    out = await forward_to_plausible(FakeHttpxClient({}), event, config)
    assert out["skipped"] is True
    config.plausible_domain = "cloudless.gr"

    url = "https://plausible.io/api/event"
    out = await forward_to_plausible(FakeHttpxClient({url: FakeResponse(500, "err")}), event, config)
    assert out["ok"] is False and out["status"] == 500

    class _Raise:
        async def post(self, *a, **kw):
            raise RuntimeError("boom")

    out = await forward_to_plausible(_Raise(), event, config)
    assert out["ok"] is False

    # default API URL when plausible_api_url unset
    config.plausible_api_url = None
    fake = FakeHttpxClient({})
    out = await forward_to_plausible(fake, event, config)
    assert out["ok"] is True
    assert fake.requests[0][1] == "https://plausible.io/api/event"


@pytest.mark.asyncio
async def test_meta_capi_mapping_and_user_data(config: WebAnalyticsConfig, event: WebAnalyticsEvent):
    from app.services.web_analytics import forward_to_meta_capi

    config.meta_pixel_id = ""
    out = await forward_to_meta_capi(FakeHttpxClient({}), event, config)
    assert out["skipped"] is True
    config.meta_pixel_id = "1234567890"

    event.event_name = "checkout_complete"  # → Purchase
    event.payload = {"email": " A@B.com ", "phone": "+30123"}
    event.client_ip = "9.9.9.9"
    event.user_agent = "UA"
    fake = FakeHttpxClient({})
    out = await forward_to_meta_capi(fake, event, config)
    assert out["ok"] is True
    data = fake.requests[0][3]["data"][0]
    assert data["event_name"] == "Purchase"
    ud = data["user_data"]
    assert ud["em"] == [hashlib.sha256(b"a@b.com").hexdigest()]
    assert ud["ph"] == [hashlib.sha256(b"+30123").hexdigest()]
    assert ud["client_ip_address"] == "9.9.9.9"

    url = "https://graph.facebook.com/v18.0/1234567890/events"
    out = await forward_to_meta_capi(FakeHttpxClient({url: FakeResponse(403, "denied")}), event, config)
    assert out["ok"] is False and out["status"] == 403
    assert out["body"] == "denied"

    class _Raise:
        async def post(self, *a, **kw):
            raise RuntimeError("capi down")

    out = await forward_to_meta_capi(_Raise(), event, config)
    assert out["ok"] is False


def test_derive_event_time_branches():
    from app.services.web_analytics import _derive_event_time

    # seconds (not ms)
    t1 = _derive_event_time({"timestamp": 1_700_000_000})
    assert t1.year == 2023
    # ISO string
    t2 = _derive_event_time({"event_time": "2024-03-01T12:00:00"})
    assert t2.year == 2024 and t2.tzinfo is not None
    # garbage → now
    t3 = _derive_event_time({"timestamp": "not-a-date"})
    assert t3.tzinfo is not None
    t4 = _derive_event_time({})
    assert t4.tzinfo is not None
    # overflowing numeric → now
    t5 = _derive_event_time({"timestamp": float("inf")})
    assert t5.tzinfo is not None


@pytest.mark.asyncio
async def test_ingest_event_forward_exception(config: WebAnalyticsConfig):
    import app.services.web_analytics as wa_module

    original = wa_module.httpx.AsyncClient

    class _Raise:
        async def __aenter__(self):
            raise RuntimeError("client fail")

        async def __aexit__(self, *a):
            return False

    wa_module.httpx.AsyncClient = lambda **kw: _Raise()  # type: ignore[assignment]
    try:
        event = await ingest_event(team_id=config.team_id, config=config, domain="d", event_name="x", payload={})
    finally:
        wa_module.httpx.AsyncClient = original  # type: ignore[misc]
    assert "error" in event.forwarded


def test_web_event_signature_verification():
    """HMAC helper produces the same value as the sender."""
    secret = "secret"
    payload = '{"event":"test"}'
    expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    from app.api.web_analytics import _verify_webhook_signature

    assert _verify_webhook_signature(payload.encode(), secret, expected) is True
    assert _verify_webhook_signature(payload.encode(), secret, "bad") is False

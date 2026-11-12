"""Coverage for the LinkedIn advanced/gated surfaces (events, ad lib, P1)."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import linkedin_ad_library as AL
from app.services import linkedin_advanced as ADV
from app.services import linkedin_events as EV
from app.worker.tasks import linkedin_lead_boost as LB


class _AsyncCM:
    """Minimal async context manager — SimpleNamespace can't be one."""

    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


@pytest.mark.asyncio
async def test_events_no_account(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))))
    monkeypatch.setattr(EV, "task_session", lambda: _AsyncCM(db))
    out = await EV.create_org_event("x", 0, 1)
    assert out["skip"] == "no-linkedin-org-account"


@pytest.mark.asyncio
async def test_create_org_event_payload_and_urn(monkeypatch):
    monkeypatch.setattr(EV, "_org_token", AsyncMock(return_value=("tok", "urn:li:organization:108614163")))
    sent: dict = {}

    class _Resp:
        status_code = 201
        text = ""
        headers = {"x-restli-id": "evt-1"}

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            sent["url"] = url
            sent["json"] = json
            sent["headers"] = kw.get("headers") or {}
            return _Resp()

    monkeypatch.setattr(EV.httpx, "AsyncClient", _Client)
    out = await EV.create_org_event(
        "Audit live session", 1732100000000, 1732102700000,
        description="ignored for now", event_url="https://cloudless.gr/contact",
    )
    assert out == {"urn": "evt-1", "status": 201}
    p = sent["json"]
    assert p["organizer"] == "urn:li:organization:108614163"
    assert p["name"] == {"localized": {"en_US": "Audit live session"}}
    assert p["startsAt"] == 1732100000000
    assert p["type"] == {"online": {"format": {"external": {"endsAt": 1732102700000, "url": "https://cloudless.gr/contact"}}}}
    # MultiLocaleRichText description is omitted — the localized-map shape 422s live
    assert "description" not in p


@pytest.mark.asyncio
async def test_create_org_event_api_error(monkeypatch):
    monkeypatch.setattr(EV, "_org_token", AsyncMock(return_value=("tok", "urn:li:organization:1")))

    class _Resp:
        status_code = 422
        text = '{"message":"bad"}'
        headers = {}

        def json(self):
            return {}

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, **kw):
            return _Resp()

    monkeypatch.setattr(EV.httpx, "AsyncClient", _Client)
    out = await EV.create_org_event("x", 1, 2)
    assert out["error"] == 422
    assert "bad" in out["body"]


@pytest.mark.asyncio
async def test_ad_library_no_token(monkeypatch):
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))))
    monkeypatch.setattr(AL, "task_session", lambda: _AsyncCM(db))
    out = await AL.search_ads(keyword="x")
    assert out["skip"] == "no-linkedin-token"


@pytest.mark.asyncio
async def test_advanced_flag_off():
    monkey = ADV
    monkey.ENABLED = False
    out = await ADV.stream_conversion(conversion_rule="x", happened_at_ms=0)
    assert out["skip"] == "disabled-or-no-account"


@pytest.mark.asyncio
async def test_route_leads_streams_lead_created_conversion(monkeypatch):
    """Successful CRM upsert must emit a lead_created conversion event."""
    import hashlib

    monkeypatch.setattr(LB, "ENABLED", True)
    acc = SimpleNamespace(team_id="t1")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: acc))))
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))

    import app.services.linkedin_ads as LADS

    monkeypatch.setattr(LADS, "ad_accounts", AsyncMock(return_value=[{"id": "a1"}]))
    monkeypatch.setattr(LADS, "lead_gen_forms", AsyncMock(return_value=[{"id": "f1"}]))
    monkeypatch.setattr(
        LADS,
        "lead_form_responses",
        AsyncMock(
            return_value=[
                {
                    "id": "resp-9",
                    "formResponse": [
                        {"question": "email", "answer": "  Lead@Example.COM "},
                        {"question": "firstName", "answer": "Ana"},
                    ],
                }
            ]
        ),
    )
    import app.services.espocrm_client as ESPO

    monkeypatch.setattr(ESPO, "upsert_lead", AsyncMock(return_value="lead-1"))
    import app.services.slack_notifications as SN

    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock())

    conv = AsyncMock(return_value={"ok": True, "status": 201})
    monkeypatch.setattr(ADV, "stream_conversion", conv)

    out = await LB._route_linkedin_leads()
    assert out["routed"] == 1
    conv.assert_awaited_once()
    kw = conv.await_args.kwargs
    assert kw["conversion_rule"] == "lead_created"
    assert kw["lead_id"] == "resp-9"
    # SHA-256 of the normalized (trimmed, lowercased) email — never raw PII
    assert kw["email_sha256"] == hashlib.sha256(b"lead@example.com").hexdigest()
    assert "lead@example.com" not in str(kw)


@pytest.mark.asyncio
async def test_route_leads_conversion_failure_never_blocks_routing(monkeypatch):
    monkeypatch.setattr(LB, "ENABLED", True)
    acc = SimpleNamespace(team_id="t1")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: acc))))
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))

    import app.services.linkedin_ads as LADS

    monkeypatch.setattr(LADS, "ad_accounts", AsyncMock(return_value=[{"id": "a1"}]))
    monkeypatch.setattr(LADS, "lead_gen_forms", AsyncMock(return_value=[{"id": "f1"}]))
    monkeypatch.setattr(
        LADS,
        "lead_form_responses",
        AsyncMock(
            return_value=[
                {
                    "id": "r2",
                    "formResponse": [{"question": "email", "answer": "b@example.com"}],
                }
            ]
        ),
    )
    import app.services.espocrm_client as ESPO

    monkeypatch.setattr(ESPO, "upsert_lead", AsyncMock(return_value="lead-2"))
    import app.services.slack_notifications as SN

    monkeypatch.setattr(SN, "post_alert_to_slack", AsyncMock())
    monkeypatch.setattr(ADV, "stream_conversion", AsyncMock(side_effect=RuntimeError("api down")))

    out = await LB._route_linkedin_leads()
    assert out["routed"] == 1  # routing succeeded despite conversion failure


@pytest.mark.asyncio
async def test_boost_flag_off():
    monkeypatch = LB
    monkeypatch.ENABLED = False
    out = await LB._route_linkedin_leads()
    assert out["skip"] == "LINKEDIN_ADVANCED off"
    out = await LB._boost_linkedin_winners()
    assert out["skip"] == "LINKEDIN_ADVANCED off"

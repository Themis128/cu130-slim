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
async def test_boost_flag_off():
    monkeypatch = LB
    monkeypatch.ENABLED = False
    out = await LB._route_linkedin_leads()
    assert out["skip"] == "LINKEDIN_ADVANCED off"
    out = await LB._boost_linkedin_winners()
    assert out["skip"] == "LINKEDIN_ADVANCED off"

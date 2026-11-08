"""Coverage batch — research endpoints, price premiums, chat retry, eval."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

pytestmark = pytest.mark.asyncio


async def test_research_endpoints(monkeypatch):
    import app.api.research as R

    monkeypatch.setattr(R.rw, "ingest_nextcloud_folder", AsyncMock(return_value={"ok": 1}))
    u = SimpleNamespace(team_id="t1")
    assert await R.ingest(SimpleNamespace(project="p", folder="f"), u) == {"ok": 1}
    monkeypatch.setattr(R.rw, "ask", AsyncMock(return_value={"a": 1}))
    assert await R.ask(SimpleNamespace(question="q", n=3), u) == {"a": 1}
    monkeypatch.setattr(R.rw, "ask", AsyncMock(side_effect=Exception("down")))
    with pytest.raises(HTTPException) as e:
        await R.ask(SimpleNamespace(question="q", n=3), u)
    assert e.value.status_code == 502
    assert (await R.status(u))["collection"] == "research_t1"


def test_dynamic_price_range_premiums():
    from app.services.agency_catalog import SERVICES, dynamic_price_range

    svcs = list(SERVICES)[:1]
    base = dynamic_price_range(svcs, complexity=0.0, concurrent_projects=0)
    high = dynamic_price_range(svcs, complexity=0.9, concurrent_projects=0)
    load = dynamic_price_range(svcs, complexity=0.0, concurrent_projects=99)
    assert high[1] > base[1] and load[0] > base[0] and load[1] > base[1]


async def test_chat_retry_then_raise(monkeypatch):
    import app.services.ai_functions as AF

    monkeypatch.setattr(AF, "_cfg", lambda: ("u", "e", "m", "em"))
    calls = {"n": 0}

    class Resp:
        def json(self):
            calls["n"] += 1
            if calls["n"] < 2:
                raise KeyError("boom")
            return {"choices": [{"message": {"content": "  answer  "}}]}

    class C:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            return Resp()

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: C())
    import asyncio

    monkeypatch.setattr(asyncio, "sleep", AsyncMock())
    out = await AF._chat("p", max_tokens=5)
    assert out == "answer" and calls["n"] == 2

    monkeypatch.setattr(Resp, "json", lambda self: (_ for _ in ()).throw(Exception("down")))
    with pytest.raises(Exception):
        await AF._chat("p", max_tokens=5)


async def test_eval_post_async(monkeypatch):
    import app.services.content_eval as CE
    import app.services.inference as INF

    monkeypatch.setattr(INF, "call_inference", AsyncMock(return_value={"response": 'pre {"total": 7} post'}))
    assert await CE.eval_post_async("post text", None) == 7
    monkeypatch.setattr(INF, "call_inference", AsyncMock(return_value={"response": "no json"}))
    assert await CE.eval_post_async("post text", None) == -1

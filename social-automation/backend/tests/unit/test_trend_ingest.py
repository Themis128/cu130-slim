"""Trend-signal ingest — feed→row mapping + degrade rules."""

import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.worker.tasks import trend_ingest as TI


def _row(query, traffic, cls):
    return {"query": query, "geo": "US", "traffic": traffic, "classification": cls, "swot": "O", "news": [{"title": "n", "source": "s"}]}


def test_google_signals_shape_and_matrix(monkeypatch):
    calls = []

    def fake_fetch(geo):
        calls.append(geo)
        return [{"query": "ai agents deploy pipeline", "geo": geo, "traffic": 5000, "pub_date": "x", "news": []}]

    monkeypatch.setattr("scripts.trendwatch.fetch_trending", fake_fetch)
    out = TI._google_signals("2026-11-08")
    assert calls == list(TI.GOOGLE_GEOS)
    assert len(out) == len(TI.GOOGLE_GEOS)
    assert out[0]["source"] == "google-trends"
    assert out[0]["period_key"] == "2026-11-08"
    assert out[0]["classification"] in ("opportunity", "early_window")


def test_google_signals_geo_failure_isolated(monkeypatch):
    def fake_fetch(geo):
        if geo == "US":
            raise RuntimeError("429")
        return [{"query": "cloud outage postmortem", "geo": geo, "traffic": 100, "news": []}]

    monkeypatch.setattr("scripts.trendwatch.fetch_trending", fake_fetch)
    out = TI._google_signals("2026-11-08")
    assert {r["geo"] for r in out} == {"GR", "GB"}  # failed geo skipped, rest kept


def test_tiktok_signals_missing_module_degrades(monkeypatch):
    import sys
    import types

    # simulate the module being absent (pre-#1422 branch layout)
    monkeypatch.delitem(sys.modules, "app.services.tiktok_trends", raising=False)
    monkeypatch.setitem(sys.modules, "app.services", types.ModuleType("app.services"))
    assert TI._tiktok_signals() == []


def test_tiktok_signals_fetch_error_degrades(monkeypatch):
    mod = types.ModuleType("app.services.tiktok_trends")
    mod.LIGHT_SWEEP = ()
    mod.fetch_trending_videos = MagicMock(side_effect=RuntimeError("tls"))
    monkeypatch.setitem(sys.modules, "app.services.tiktok_trends", mod)
    assert TI._tiktok_signals() == []


def test_tiktok_signals_maps_rows(monkeypatch):
    mod = types.ModuleType("app.services.tiktok_trends")
    mod.LIGHT_SWEEP = ("light",)
    mod.fetch_trending_videos = MagicMock(return_value=[{"q": "x"}])
    monkeypatch.setitem(sys.modules, "app.services.tiktok_trends", mod)
    monkeypatch.setattr(
        "scripts.trendwatch.build_matrix",
        lambda videos: [
            {
                "query": "ai agents",
                "geo": "US",
                "traffic": 900,
                "classification": "opportunity",
                "swot": "O",
                "tiktok": {"week_ending": "2026-11-09", "rank": 3, "also_ranked": ["a", "b"]},
            }
        ],
    )
    out = TI._tiktok_signals()
    assert len(out) == 1
    row = out[0]
    assert row["source"] == "tiktok-cc"
    assert row["period_key"] == "2026-11-09"
    assert row["meta_data"]["tiktok"] == {"week_ending": "2026-11-09", "rank": 3}  # also_ranked stripped
    mod.fetch_trending_videos.assert_called_once_with(sweep=("light",))


class _FakeSession:
    def __init__(self, rowcount=0):
        self.rowcount = rowcount
        self.stmt = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, stmt):
        self.stmt = stmt
        return SimpleNamespace(rowcount=self.rowcount)

    async def commit(self):
        pass


def _signal(**kw):
    base = {
        "source": "google-trends",
        "query": "q",
        "geo": "GR",
        "traffic": 10,
        "classification": "opportunity",
        "swot": "O",
        "period_key": "2026-11-08",
        "meta_data": {},
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_run_upserts_signals(monkeypatch):
    import app.db.session as session_mod

    monkeypatch.setattr(TI, "_google_signals", lambda today: [_signal()])
    monkeypatch.setattr(TI, "_tiktok_signals", lambda: [_signal(source="tiktok-cc")])
    sess = _FakeSession(rowcount=2)
    monkeypatch.setattr(session_mod, "async_session_maker", lambda: sess)
    out = await TI._run()
    assert out == {"fetched": 2, "inserted": 2}
    assert sess.stmt is not None


@pytest.mark.asyncio
async def test_run_no_signals_skips_db(monkeypatch):
    import app.db.session as session_mod

    monkeypatch.setattr(TI, "_google_signals", lambda today: [])
    monkeypatch.setattr(TI, "_tiktok_signals", lambda: [])
    monkeypatch.setattr(session_mod, "async_session_maker", MagicMock(side_effect=AssertionError("session must not open")))
    assert await TI._run() == {"inserted": 0, "skipped": "no signals"}


def test_ingest_trends_task_delegates(monkeypatch):
    monkeypatch.setattr(TI, "_run", MagicMock(return_value="C"))
    monkeypatch.setattr("app.worker._async.run_async", lambda coro: "done")
    assert TI.ingest_trends() == "done"

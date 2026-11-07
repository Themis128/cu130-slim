"""Trend-signal ingest — feed→row mapping + degrade rules."""

from unittest.mock import MagicMock

from app.worker.tasks import trend_ingest as TI


def _row(query, traffic, cls):
    return {"query": query, "geo": "US", "traffic": traffic,
            "classification": cls, "swot": "O", "news": [{"title": "n", "source": "s"}]}


def test_google_signals_shape_and_matrix(monkeypatch):
    calls = []

    def fake_fetch(geo):
        calls.append(geo)
        return [{"query": "ai agents deploy pipeline", "geo": geo,
                 "traffic": 5000, "pub_date": "x", "news": []}]

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
    import types

    mod = types.ModuleType("app.services.tiktok_trends")
    mod.LIGHT_SWEEP = ()
    mod.fetch_trending_videos = MagicMock(side_effect=RuntimeError("tls"))
    monkeypatch.setitem(__import__("sys").modules, "app.services.tiktok_trends", mod)
    assert TI._tiktok_signals() == []

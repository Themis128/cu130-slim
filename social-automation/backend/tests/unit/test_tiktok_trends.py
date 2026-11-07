"""TikTok Creative Center trending feed — response contract + dedup +
fail-open signal paths. The CC endpoint contract was verified live
2026-11-08 (unsigned GET, 4 items/call, BaseResp status wrapper)."""

from unittest.mock import MagicMock

import httpx
import pytest

from app.services import tiktok_trends as T
from app.services import trend_context

OVERVIEW = {"lastWeeklyEndTimestamp": "1793491200"}


def _item(item_id, title, views="5000000", er=0.02, handle="acme"):
    return {
        "itemInfo": {"itemID": item_id, "title": title, "contentType": 1},
        "itemAuthorInfo": {"handlerName": handle, "bio": f"@{handle} bio"},
        "itemAuthorMetrics": {"followers": "120000"},
        "itemMetrics": {
            "videoViews": views,
            "organicVideoViews": "4000000",
            "engagementRate": er,
            "sixSecondsVTR": 0.03,
        },
    }


def _client(payloads: dict[str, object]):
    def handler(req: httpx.Request) -> httpx.Response:
        for key, body in payloads.items():
            if key in req.url.path or key in str(req.url):
                return httpx.Response(200, json=body)
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_normalize_shape():
    sig = T._normalize(
        _item("42", "how ai automation saved my startup 40 hours"),
        {"orderByMetric": 1, "contentLabelIDs": "11015", "organicOnly": "false"},
        "1793491200",
    )
    assert sig["query"].startswith("how ai automation")
    assert sig["geo"] == "TT-US"
    assert sig["traffic"] == 5_000_000
    assert sig["source"] == "tiktok-creative-center"
    assert sig["tiktok"]["url"].endswith("/@acme/video/42")
    assert sig["tiktok"]["label"] == "Technology & Finance"


def test_fetch_dedupes_and_merges_rankings():
    item = _item("42", "ai dashboard build")
    payloads = {
        "GetTopContentsOverview": OVERVIEW,
        "CreativeCenterGetTopContentsList": {
            "BaseResp": {"StatusCode": 0},
            "entityInfos": [item],
            "pagination": {},
        },
    }
    sweep = (
        {"orderByMetric": 1, "contentLabelIDs": "", "organicOnly": "false"},
        {"orderByMetric": 2, "contentLabelIDs": "11015", "organicOnly": "false"},
    )
    out = T.fetch_trending_videos(sweep=sweep, client=_client(payloads), pace=0)
    assert len(out) == 1
    assert out[0]["tiktok"]["also_ranked"], "same item under second combo merged"


def test_fetch_errors_on_base_resp_status():
    payloads = {
        "GetTopContentsOverview": OVERVIEW,
        "CreativeCenterGetTopContentsList": {
            "BaseResp": {"StatusCode": 38014004, "StatusMessage": "bad params"},
            "entityInfos": [],
        },
    }
    sweep = ({"orderByMetric": 1, "contentLabelIDs": "", "organicOnly": "false"},)
    with pytest.raises(RuntimeError, match="38014004"):
        T.fetch_trending_videos(sweep=sweep, client=_client(payloads), pace=0)


def test_fetch_requires_week_timestamp():
    payloads = {"GetTopContentsOverview": {}}
    sweep = ({"orderByMetric": 1, "contentLabelIDs": "", "organicOnly": "false"},)
    with pytest.raises(RuntimeError, match="weekly timestamp"):
        T.fetch_trending_videos(sweep=sweep, client=_client(payloads), pace=0)


def test_top_tiktok_signal_picks_niche_hook(monkeypatch):
    trend_context._tt_cache = (0.0, None)
    niche = T._normalize(
        _item("1", "ai agents run my whole cloud deploy pipeline", views="9000000"),
        {"orderByMetric": 1, "contentLabelIDs": "", "organicOnly": "false"},
        "x",
    )
    noise = T._normalize(
        _item("2", "my grandma tried the mariah song", views="50000000"),
        {"orderByMetric": 1, "contentLabelIDs": "", "organicOnly": "false"},
        "x",
    )
    monkeypatch.setattr(
        "app.services.tiktok_trends.fetch_trending_videos",
        MagicMock(return_value=[noise, niche]),
    )
    sig = trend_context.top_tiktok_signal()
    assert sig and "ai agents" in sig
    assert trend_context.top_tiktok_signal() == sig  # cached


def test_top_tiktok_signal_fails_open(monkeypatch):
    trend_context._tt_cache = (0.0, None)
    monkeypatch.setattr(
        "app.services.tiktok_trends.fetch_trending_videos",
        MagicMock(side_effect=RuntimeError("tls reset")),
    )
    assert trend_context.top_tiktok_signal() is None


def test_trend_line_routes_platform(monkeypatch):
    trend_context._tt_cache = (0.0, None)
    monkeypatch.setattr(trend_context, "top_tiktok_signal", lambda: "tiktok-native hook")
    monkeypatch.setattr(trend_context, "top_relevant_signal", lambda *a, **k: "google signal")
    assert "tiktok-native hook" in trend_context.trend_line("tiktok")
    assert "google signal" in trend_context.trend_line("linkedin")
    # tiktok feed empty → falls back to google signal rather than nothing
    monkeypatch.setattr(trend_context, "top_tiktok_signal", lambda: None)
    assert "google signal" in trend_context.trend_line("tiktok")


def test_scenarios_carry_tiktok_format():
    from scripts.tiktok_trendwatch import FORMAT_GUIDE, model_scenarios

    rows = [
        {
            "query": "ai workflow that rebuilt my entire crm",
            "geo": "TT-US",
            "traffic": 2_000_000,
            "relevant": True,
            "classification": "opportunity",
            "swot": "O",
            "tiktok": {"url": "https://www.tiktok.com/@x/video/1", "author": "x", "engagement_rate": 0.04},
        }
    ]
    sc = model_scenarios(rows)
    assert sc[0]["channel"] == "tiktok"
    assert sc[0]["strategy"] == "leader"
    assert "is_aigc" in FORMAT_GUIDE
    assert "trendwatch" not in sc[0]["cta"] or "utm_source=tiktok" in sc[0]["cta"]

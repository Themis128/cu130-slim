"""Unit tests for Meta insights adaptive metric dropping."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.services.analytics_sync import (
    _meta_insights_get,
    _meta_unsupported_metric_names,
)


@pytest.mark.parametrize(
    "msg,expected",
    [
        (
            "The Instagram Media Insights API does not support the impressions, replies metric for this media product type",
            {"impressions", "replies"},
        ),
        (
            "The impressions, replies metrics are not available for this media product type",
            {"impressions", "replies"},
        ),
        (
            "(#100) metric impressions is not available for this media product type",
            {"impressions"},
        ),
        ("unrelated error", set()),
    ],
)
def test_meta_unsupported_metric_names(msg: str, expected: set[str]):
    assert _meta_unsupported_metric_names(msg) == expected


class _FakeResponse:
    def __init__(self, status_code: int, body: dict[str, Any]):
        self.status_code = status_code
        self._body = body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _FakeClient:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def get(self, url: str, headers=None, params=None):
        self.calls.append({"url": url, "params": dict(params or {})})
        if not self.responses:
            raise AssertionError("unexpected extra request")
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_meta_insights_get_drops_product_type_metrics_and_retries():
    client = _FakeClient(
        [
            _FakeResponse(
                400,
                {
                    "error": {
                        "message": (
                            "The Instagram Media Insights API does not support "
                            "the impressions, replies metric for this media product type"
                        )
                    }
                },
            ),
            _FakeResponse(200, {"data": [{"name": "views", "values": [{"value": 9}]}]}),
        ]
    )
    resp = await _meta_insights_get(
        client,  # type: ignore[arg-type]
        "https://graph.instagram.com/v26.0/1/insights",
        ["views", "impressions", "replies", "likes"],
        params={"access_token": "tok"},
    )
    assert resp.status_code == 200
    assert len(client.calls) == 2
    assert client.calls[0]["params"]["metric"] == "views,impressions,replies,likes"
    assert client.calls[1]["params"]["metric"] == "views,likes"


@pytest.mark.asyncio
async def test_meta_insights_get_drops_metric_index():
    client = _FakeClient(
        [
            _FakeResponse(
                400,
                {
                    "error": {
                        "message": (
                            "metric[1] must be one of the following values: views,likes"
                        )
                    }
                },
            ),
            _FakeResponse(200, {"data": []}),
        ]
    )
    resp = await _meta_insights_get(
        client,  # type: ignore[arg-type]
        "https://example.test/insights",
        ["views", "impressions", "likes"],
    )
    assert resp.status_code == 200
    assert client.calls[1]["params"]["metric"] == "views,likes"


@pytest.mark.asyncio
async def test_meta_insights_get_bare_100_probes_metrics_individually():
    """'(#100) The value must be a valid insights metric' rejects the whole
    batch without naming offenders (post-type doesn't support some metrics) —
    recover by probing each metric alone and merging the ones that work."""
    client = _FakeClient(
        [
            _FakeResponse(
                400,
                {"error": {"message": "(#100) The value must be a valid insights metric"}},
            ),
            # post_media_view invalid for this post type → 400
            _FakeResponse(400, {"error": {"message": "(#100) nope"}}),
            # post_clicks works → 200
            _FakeResponse(200, {"data": [{"name": "post_clicks", "values": [{"value": 3}]}]}),
        ]
    )
    resp = await _meta_insights_get(
        client,  # type: ignore[arg-type]
        "https://graph.facebook.com/v26.0/1/insights",
        ["post_media_view", "post_clicks"],
    )
    assert resp.status_code == 200
    assert resp.json() == {
        "data": [{"name": "post_clicks", "values": [{"value": 3}]}]
    }
    assert [c["params"]["metric"] for c in client.calls] == [
        "post_media_view,post_clicks",
        "post_media_view",
        "post_clicks",
    ]


@pytest.mark.asyncio
async def test_meta_insights_get_bare_100_all_invalid_returns_last_error():
    client = _FakeClient(
        [
            _FakeResponse(
                400,
                {"error": {"message": "(#100) The value must be a valid insights metric"}},
            ),
            _FakeResponse(400, {"error": {"message": "(#100) nope"}}),
            _FakeResponse(400, {"error": {"message": "(#100) nope"}}),
        ]
    )
    resp = await _meta_insights_get(
        client,  # type: ignore[arg-type]
        "https://graph.facebook.com/v26.0/1/insights",
        ["post_media_view", "post_video_views"],
    )
    assert resp.status_code == 400

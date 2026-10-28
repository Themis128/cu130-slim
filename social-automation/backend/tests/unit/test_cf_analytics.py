"""Unit tests for Cloudflare GraphQL analytics helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services import cf_analytics


@pytest.mark.asyncio
async def test_graphql_query_returns_none_without_credentials(monkeypatch):
    monkeypatch.setattr(cf_analytics.settings, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(cf_analytics.settings, "CLOUDFLARE_ACCOUNT_ID", "")
    result = await cf_analytics._graphql_query("query { viewer { id } }", {})
    assert result is None


@pytest.mark.asyncio
async def test_workers_ai_usage_empty_on_api_failure():
    with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=None)):
        result = await cf_analytics.get_workers_ai_usage(days=7)
    assert result["total_requests"] == 0
    assert result["total_neurons"] == 0
    assert result["by_model"] == []
    assert result["by_day"] == []


@pytest.mark.asyncio
async def test_workers_ai_usage_aggregates_rows():
    payload = {
        "viewer": {
            "accounts": [
                {
                    "aiInferenceAdaptiveGroups": [
                        {
                            "count": 2,
                            "sum": {
                                "totalInputTokens": 1000,
                                "totalOutputTokens": 500,
                            },
                            "dimensions": {
                                "modelId": "@cf/meta/llama-3.1-8b-instruct",
                                "datetimeHour": "2026-09-12T10:00:00Z",
                            },
                        },
                        {
                            "count": 1,
                            "sum": {
                                "totalInputTokens": 500,
                                "totalOutputTokens": 0,
                            },
                            "dimensions": {
                                "modelId": "@cf/meta/llama-3.1-8b-instruct",
                                "datetimeHour": "2026-09-13T11:00:00Z",
                            },
                        },
                    ]
                }
            ]
        }
    }
    with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)):
        result = await cf_analytics.get_workers_ai_usage(days=2)

    assert result["total_requests"] == 3
    # (1500 + 500) // 500 = 4 neurons across both rows
    assert result["total_neurons"] == 4
    assert len(result["by_model"]) == 1
    assert result["by_model"][0]["model"] == "@cf/meta/llama-3.1-8b-instruct"
    assert result["by_model"][0]["requests"] == 3
    assert len(result["by_day"]) == 2


@pytest.mark.asyncio
async def test_vectorize_usage_no_nameerror_on_rows():
    payload = {
        "viewer": {
            "accounts": [
                {
                    "vectorizeQueriesAdaptiveGroups": [
                        {
                            "sum": {
                                "vectorsQueried": 10,
                                "vectorsInserted": 3,
                            },
                            "dimensions": {
                                "datetime": "2026-09-13T00:00:00Z",
                            },
                        }
                    ]
                }
            ]
        }
    }
    with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)):
        result = await cf_analytics.get_vectorize_usage(days=1)

    assert result["total_vectors_queried"] == 10
    assert result["total_vectors_inserted"] == 3
    assert result["total_queries"] == 13
    assert result["by_index"][0]["index"] == "2026-09-13T00:00:00Z"
    assert result["by_index"][0]["queries"] == 13


@pytest.mark.asyncio
async def test_cf_overview_gathers_all_sections():
    empty_ai = {
        "total_requests": 1,
        "total_neurons": 100,
        "total_errors": 0,
        "by_model": [],
        "by_day": [],
    }
    empty_workers = {"total_requests": 5, "total_errors": 0, "by_script": []}
    empty_r2 = {
        "total_operations": 2,
        "by_action": [],
        "by_bucket": [],
        "total_storage_bytes": 0,
    }
    empty_d1 = {"total_queries": 3, "by_database": []}
    empty_kv = {"total_operations": 4, "by_action": []}
    empty_vz = {
        "total_queries": 0,
        "total_vectors_queried": 0,
        "total_vectors_inserted": 0,
        "by_index": [],
    }
    empty_ai_gw = {"total_requests": 7, "by_gateway": [], "by_provider": []}

    with (
        patch.object(
            cf_analytics,
            "get_workers_ai_usage",
            new=AsyncMock(return_value=empty_ai),
        ),
        patch.object(
            cf_analytics,
            "get_workers_invocations",
            new=AsyncMock(return_value=empty_workers),
        ),
        patch.object(cf_analytics, "get_r2_usage", new=AsyncMock(return_value=empty_r2)),
        patch.object(cf_analytics, "get_d1_usage", new=AsyncMock(return_value=empty_d1)),
        patch.object(cf_analytics, "get_kv_usage", new=AsyncMock(return_value=empty_kv)),
        patch.object(
            cf_analytics,
            "get_vectorize_usage",
            new=AsyncMock(return_value=empty_vz),
        ),
        patch.object(
            cf_analytics,
            "get_ai_gateway_usage",
            new=AsyncMock(return_value=empty_ai_gw),
        ),
    ):
        overview = await cf_analytics.get_cf_overview(days=7)

    assert overview["workers_ai"]["total_requests"] == 1
    assert overview["workers_ai"]["free_tier_limit"] == 70000
    assert overview["workers_ai"]["free_tier_remaining"] == 69900
    assert overview["workers"]["total_requests"] == 5
    assert overview["r2"]["total_operations"] == 2
    assert overview["d1"]["total_queries"] == 3
    assert overview["kv"]["total_operations"] == 4
    assert set(overview.keys()) == {
        "workers_ai",
        "workers",
        "r2",
        "d1",
        "kv",
        "vectorize",
        "ai_gateway",
    }


# ---------------------------------------------------------------------------
# Extended coverage: _graphql_query HTTP paths + remaining dataset parsers
# ---------------------------------------------------------------------------
class _HTTP:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, **kw):
        self.posts.append((url, kw))
        if self._exc:
            raise self._exc
        return self._resp


def _resp(status, body):
    return SimpleNamespace(status_code=status, json=lambda: body)


def _creds(monkeypatch):
    monkeypatch.setattr(cf_analytics.settings, "CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setattr(cf_analytics.settings, "CLOUDFLARE_ACCOUNT_ID", "acc")


@pytest.mark.asyncio
async def test_graphql_non200(monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setattr(cf_analytics.httpx, "AsyncClient", lambda **kw: _HTTP(resp=_resp(500, {})))
    assert await cf_analytics._graphql_query("q", {}) is None


@pytest.mark.asyncio
async def test_graphql_errors_field(monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setattr(cf_analytics.httpx, "AsyncClient", lambda **kw: _HTTP(resp=_resp(200, {"errors": [{"m": "bad"}]})))
    assert await cf_analytics._graphql_query("q", {}) is None


@pytest.mark.asyncio
async def test_graphql_success(monkeypatch):
    _creds(monkeypatch)
    c = _HTTP(resp=_resp(200, {"data": {"viewer": {}}}))
    monkeypatch.setattr(cf_analytics.httpx, "AsyncClient", lambda **kw: c)
    assert await cf_analytics._graphql_query("q", {"v": 1}) == {"viewer": {}}
    assert "Bearer tok" in c.posts[0][1]["headers"]["Authorization"]


@pytest.mark.asyncio
async def test_graphql_exception(monkeypatch):
    _creds(monkeypatch)
    monkeypatch.setattr(cf_analytics.httpx, "AsyncClient", lambda **kw: _HTTP(exc=OSError("net")))
    assert await cf_analytics._graphql_query("q", {}) is None


def _accounts(**groups):
    return {"viewer": {"accounts": [groups]}}


@pytest.mark.asyncio
async def test_workers_ai_empty_accounts():
    with patch.object(
        cf_analytics,
        "_graphql_query",
        new=AsyncMock(return_value={"viewer": {"accounts": []}}),
    ):
        result = await cf_analytics.get_workers_ai_usage()
    assert result["total_requests"] == 0


@pytest.mark.asyncio
async def test_workers_invocations_rows():
    payload = _accounts(
        workersInvocationsAdaptive=[
            {
                "sum": {"requests": 10, "errors": 1},
                "quantiles": {"cpuTimeP50": 5, "cpuTimeP99": 20},
                "dimensions": {"scriptName": "cloudless2", "status": "ok"},
            },
            {"sum": {"requests": 4, "errors": 2}, "quantiles": {"cpuTimeP50": 50, "cpuTimeP99": 90}, "dimensions": {"scriptName": "cloudless2"}},
            {"sum": {"requests": 1}, "dimensions": {}},
        ]
    )
    with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)):
        out = await cf_analytics.get_workers_invocations()
    assert out["total_requests"] == 15
    assert out["total_errors"] == 3
    s = out["by_script"][0]
    assert s["script"] == "cloudless2"
    assert s["cpu_time_p50"] == 50 and s["cpu_time_p99"] == 90
    assert out["by_script"][1]["script"] == "unknown"


@pytest.mark.asyncio
async def test_workers_invocations_empty():
    for ret in (None, {"viewer": {"accounts": []}}):
        with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=ret)):
            out = await cf_analytics.get_workers_invocations()
        assert out["total_requests"] == 0 and out["by_script"] == []


@pytest.mark.asyncio
async def test_r2_usage_rows():
    payload = _accounts(
        r2OperationsAdaptiveGroups=[
            {"sum": {"requests": 100}, "dimensions": {"actionType": "GetObject", "bucketName": "media"}},
            {"sum": {"requests": 40}, "dimensions": {"actionType": "PutObject", "bucketName": "media"}},
            {"sum": {"requests": 5}, "dimensions": {"actionType": "GetObject", "bucketName": "backup"}},
        ]
    )
    with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)):
        out = await cf_analytics.get_r2_usage()
    assert out["total_operations"] == 145
    assert out["by_action"][0]["action"] == "GetObject"
    assert out["by_action"][0]["requests"] == 105
    assert out["by_bucket"][0]["bucket"] == "media"
    assert out["by_bucket"][0]["operations"] == 140


@pytest.mark.asyncio
async def test_r2_usage_empty():
    for ret in (None, {"viewer": {"accounts": []}}):
        with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=ret)):
            out = await cf_analytics.get_r2_usage()
        assert out["total_operations"] == 0


@pytest.mark.asyncio
async def test_d1_usage_rows():
    payload = _accounts(
        d1QueriesAdaptiveGroups=[
            {"count": 50, "dimensions": {"databaseId": "db1"}},
            {"count": 30, "dimensions": {"databaseId": "db2"}},
            {"count": 20, "dimensions": {"databaseId": "db1"}},
        ]
    )
    with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)):
        out = await cf_analytics.get_d1_usage()
    assert out["total_queries"] == 100
    assert out["by_database"][0]["database"] == "db1"
    assert out["by_database"][0]["queries"] == 70


@pytest.mark.asyncio
async def test_d1_usage_empty():
    for ret in (None, {"viewer": {"accounts": []}}):
        with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=ret)):
            out = await cf_analytics.get_d1_usage()
        assert out["total_queries"] == 0


@pytest.mark.asyncio
async def test_kv_usage_rows():
    payload = _accounts(
        kvOperationsAdaptiveGroups=[
            {"sum": {"requests": 500}, "dimensions": {"actionType": "read"}},
            {"sum": {"requests": 50}, "dimensions": {"actionType": "write"}},
            {"sum": {"requests": 10}, "dimensions": {}},
        ]
    )
    with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)):
        out = await cf_analytics.get_kv_usage()
    assert out["total_operations"] == 560
    assert out["by_action"][0]["action"] == "read"
    assert out["by_action"][2]["action"] == "unknown"


@pytest.mark.asyncio
async def test_kv_usage_empty():
    for ret in (None, {"viewer": {"accounts": []}}):
        with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=ret)):
            out = await cf_analytics.get_kv_usage()
        assert out["total_operations"] == 0


@pytest.mark.asyncio
async def test_vectorize_empty():
    for ret in (None, {"viewer": {"accounts": []}}):
        with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=ret)):
            out = await cf_analytics.get_vectorize_usage()
        assert out["total_queries"] == 0 and out["by_index"] == []


@pytest.mark.asyncio
async def test_ai_gateway_rows():
    payload = _accounts(
        aiGatewayRequestsAdaptiveGroups=[
            {
                "count": 10,
                "sum": {"cachedTokensIn": 100, "cachedTokensOut": 20, "uncachedTokensIn": 400, "uncachedTokensOut": 80},
                "dimensions": {"gateway": "gw1", "provider": "workers-ai", "model": "llama"},
            },
            {"count": 5, "sum": {}, "dimensions": {"gateway": "gw1", "provider": "openai", "model": "gpt"}},
            {"count": 2, "dimensions": {}},
        ]
    )
    with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)):
        out = await cf_analytics.get_ai_gateway_usage()
    assert out["total_requests"] == 17
    g = out["by_gateway"][0]
    assert g["gateway"] == "gw1"
    assert g["requests"] == 15
    assert g["cached_tokens"] == 120
    assert g["uncached_tokens"] == 480
    assert g["models"] == [{"model": "llama", "requests": 10}, {"model": "gpt", "requests": 5}]
    assert out["by_provider"][0]["provider"] == "workers-ai"
    assert out["by_gateway"][1]["gateway"] == "unknown"


@pytest.mark.asyncio
async def test_ai_gateway_empty():
    for ret in (None, {"viewer": {"accounts": []}}):
        with patch.object(cf_analytics, "_graphql_query", new=AsyncMock(return_value=ret)):
            out = await cf_analytics.get_ai_gateway_usage()
        assert out["total_requests"] == 0

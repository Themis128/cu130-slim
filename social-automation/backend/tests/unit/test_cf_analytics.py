"""Unit tests for Cloudflare GraphQL analytics helpers."""

from __future__ import annotations

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
    with patch.object(
        cf_analytics, "_graphql_query", new=AsyncMock(return_value=None)
    ):
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
    with patch.object(
        cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)
    ):
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
                                "indexName": "brand-index",
                                "datetime": "2026-09-13T00:00:00Z",
                            },
                        }
                    ]
                }
            ]
        }
    }
    with patch.object(
        cf_analytics, "_graphql_query", new=AsyncMock(return_value=payload)
    ):
        result = await cf_analytics.get_vectorize_usage(days=1)

    assert result["total_vectors_queried"] == 10
    assert result["total_vectors_inserted"] == 3
    assert result["total_queries"] == 13
    assert result["by_index"][0]["index"] == "brand-index"
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
        patch.object(
            cf_analytics, "get_r2_usage", new=AsyncMock(return_value=empty_r2)
        ),
        patch.object(
            cf_analytics, "get_d1_usage", new=AsyncMock(return_value=empty_d1)
        ),
        patch.object(
            cf_analytics, "get_kv_usage", new=AsyncMock(return_value=empty_kv)
        ),
        patch.object(
            cf_analytics,
            "get_vectorize_usage",
            new=AsyncMock(return_value=empty_vz),
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
    }

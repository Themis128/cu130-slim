"""Cloudflare Analytics API client.

Fetches Workers AI usage metrics from the Cloudflare GraphQL Analytics API.
Free — uses existing CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID.

Docs: https://developers.cloudflare.com/analytics/graphql-api/
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_GRAPHQL_URL = "https://api.cloudflare.com/client/v4/graphql"


async def _graphql_query(
    query: str, variables: dict[str, Any]
) -> dict | None:
    """Execute a GraphQL query against the Cloudflare Analytics API."""
    if not settings.CLOUDFLARE_API_TOKEN or not settings.CLOUDFLARE_ACCOUNT_ID:
        logger.debug("Cloudflare credentials not configured for analytics")
        return None
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                _GRAPHQL_URL,
                headers={
                    "Authorization": f"Bearer {settings.CLOUDFLARE_API_TOKEN}",
                    "Content-Type": "application/json",
                },
                json={"query": query, "variables": variables},
            )
            if resp.status_code != 200:
                logger.warning(
                    "CF Analytics API returned %d", resp.status_code
                )
                return None
            data = resp.json()
            if data.get("errors"):
                logger.warning(
                    "CF Analytics GraphQL errors: %s", data["errors"]
                )
                return None
            return data.get("data")
    except Exception as exc:
        logger.warning("CF Analytics query failed: %s", exc)
        return None


async def get_workers_ai_usage(days: int = 7) -> dict[str, Any]:
    """Fetch Workers AI usage metrics for the last N days."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    query = """
    query GetWorkersAIUsage($accountTag: string!,
                            $datetimeStart: string!,
                            $datetimeEnd: string!) {
      viewer {
        accounts(filter: {accountTag: $accountTag}) {
          aiInferenceAdaptiveGroups(
            limit: 10000
            filter: {datetime_geq: $datetimeStart,
                     datetime_leq: $datetimeEnd}
            orderBy: [datetimeHour_DESC]
          ) {
            count
            sum { totalInputTokens totalOutputTokens totalRequestBytesIn }
            dimensions { modelId datetimeHour }
          }
        }
      }
    }
    """
    data = await _graphql_query(query, {
        "accountTag": settings.CLOUDFLARE_ACCOUNT_ID,
        "datetimeStart": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "datetimeEnd": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    })
    if not data:
        return {
            "total_requests": 0, "total_neurons": 0,
            "total_errors": 0, "by_model": [], "by_day": [],
        }

    accounts = data.get("viewer", {}).get("accounts", [])
    if not accounts:
        return {
            "total_requests": 0, "total_neurons": 0,
            "total_errors": 0, "by_model": [], "by_day": [],
        }

    rows = accounts[0].get("aiInferenceAdaptiveGroups", [])
    by_model: dict[str, dict] = {}
    by_day: dict[str, dict] = {}
    total_requests = 0
    total_neurons = 0

    for row in rows:
        dims = row.get("dimensions", {})
        sums = row.get("sum", {})
        count = row.get("count", 0) or 0
        model = dims.get("modelId", "unknown")
        dt = dims.get("datetimeHour", "")
        day = dt[:10] if dt else ""
        input_tokens = sums.get("totalInputTokens", 0) or 0
        output_tokens = sums.get("totalOutputTokens", 0) or 0
        neurons = (input_tokens + output_tokens) // 500

        total_requests += count
        total_neurons += neurons

        if model not in by_model:
            by_model[model] = {
                "model": model, "requests": 0,
                "neurons": 0, "errors": 0,
            }
        by_model[model]["requests"] += count
        by_model[model]["neurons"] += neurons

        if day:
            if day not in by_day:
                by_day[day] = {"date": day, "requests": 0, "neurons": 0}
            by_day[day]["requests"] += count
            by_day[day]["neurons"] += neurons

    return {
        "total_requests": total_requests,
        "total_neurons": total_neurons,
        "total_errors": 0,
        "by_model": sorted(
            by_model.values(), key=lambda x: x["neurons"], reverse=True
        ),
        "by_day": sorted(by_day.values(), key=lambda x: x["date"]),
    }


async def get_workers_invocations(days: int = 7) -> dict[str, Any]:
    """Fetch Workers invocation metrics (CPU time, requests, errors)."""
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    query = """
    query GetWorkersInvocations($accountTag: string!,
                                 $datetimeStart: string!,
                                 $datetimeEnd: string!) {
      viewer {
        accounts(filter: {accountTag: $accountTag}) {
          workersInvocationsAdaptive(
            limit: 10000
            filter: {datetime_geq: $datetimeStart,
                     datetime_leq: $datetimeEnd}
          ) {
            sum { requests errors subrequests }
            quantiles { cpuTimeP50 cpuTimeP99 }
            dimensions { scriptName status }
          }
        }
      }
    }
    """
    data = await _graphql_query(query, {
        "accountTag": settings.CLOUDFLARE_ACCOUNT_ID,
        "datetimeStart": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "datetimeEnd": end.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    })
    if not data:
        return {
            "total_requests": 0, "total_errors": 0, "by_script": [],
        }

    accounts = data.get("viewer", {}).get("accounts", [])
    if not accounts:
        return {
            "total_requests": 0, "total_errors": 0, "by_script": [],
        }

    rows = accounts[0].get("workersInvocationsAdaptive", [])
    by_script: dict[str, dict] = {}
    total_requests = 0
    total_errors = 0

    for row in rows:
        dims = row.get("dimensions", {})
        sums = row.get("sum", {})
        quants = row.get("quantiles", {})
        script = dims.get("scriptName", "unknown")
        req = sums.get("requests", 0) or 0
        err = sums.get("errors", 0) or 0
        cpu50 = quants.get("cpuTimeP50", 0) or 0
        cpu99 = quants.get("cpuTimeP99", 0) or 0

        total_requests += req
        total_errors += err

        if script not in by_script:
            by_script[script] = {
                "script": script, "requests": 0, "errors": 0,
                "cpu_time_p50": 0, "cpu_time_p99": 0,
            }
        by_script[script]["requests"] += req
        by_script[script]["errors"] += err
        by_script[script]["cpu_time_p50"] = max(
            by_script[script]["cpu_time_p50"], cpu50
        )
        by_script[script]["cpu_time_p99"] = max(
            by_script[script]["cpu_time_p99"], cpu99
        )

    return {
        "total_requests": total_requests,
        "total_errors": total_errors,
        "by_script": sorted(
            by_script.values(), key=lambda x: x["requests"], reverse=True
        ),
    }

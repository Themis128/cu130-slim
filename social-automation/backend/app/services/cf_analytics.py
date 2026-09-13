"""Cloudflare Analytics API client.

Fetches Workers AI usage metrics from the Cloudflare GraphQL Analytics API.
Free — uses existing CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID.

Docs: https://developers.cloudflare.com/analytics/graphql-api/
"""
from __future__ import annotations

import asyncio
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


async def get_r2_usage(days: int = 7) -> dict[str, Any]:
    """Fetch R2 storage and operations metrics.

    Datasets:
    - r2OperationsAdaptiveGroups: operation counts by action type
    - r2StorageAdaptiveGroups: storage size by bucket
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    query = """
    query GetR2Usage($accountTag: string!,
                     $datetimeStart: string!,
                     $datetimeEnd: string!) {
      viewer {
        accounts(filter: {accountTag: $accountTag}) {
          r2OperationsAdaptiveGroups(
            limit: 10000
            filter: {datetime_geq: $datetimeStart,
                     datetime_leq: $datetimeEnd}
          ) {
            sum { requests }
            dimensions { actionType bucketName }
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
            "total_operations": 0, "by_action": [],
            "by_bucket": [], "total_storage_bytes": 0,
        }

    accounts = data.get("viewer", {}).get("accounts", [])
    if not accounts:
        return {
            "total_operations": 0, "by_action": [],
            "by_bucket": [], "total_storage_bytes": 0,
        }

    op_rows = accounts[0].get("r2OperationsAdaptiveGroups", [])

    by_action: dict[str, dict] = {}
    by_bucket: dict[str, dict] = {}
    total_operations = 0
    total_storage = 0

    for row in op_rows:
        dims = row.get("dimensions", {})
        sums = row.get("sum", {})
        action = dims.get("actionType", "unknown")
        bucket = dims.get("bucketName", "unknown")
        req = sums.get("requests", 0) or 0

        total_operations += req

        if action not in by_action:
            by_action[action] = {
                "action": action, "requests": 0,
            }
        by_action[action]["requests"] += req

        if bucket not in by_bucket:
            by_bucket[bucket] = {
                "bucket": bucket, "operations": 0,
                "storage_bytes": 0,
            }
        by_bucket[bucket]["operations"] += req

    return {
        "total_operations": total_operations,
        "by_action": sorted(
            by_action.values(),
            key=lambda x: x["requests"],
            reverse=True,
        ),
        "by_bucket": sorted(
            by_bucket.values(),
            key=lambda x: x["operations"],
            reverse=True,
        ),
        "total_storage_bytes": total_storage,
    }


async def get_d1_usage(days: int = 7) -> dict[str, Any]:
    """Fetch D1 database query and storage metrics.

    Datasets:
    - d1QueriesAdaptiveGroups: query counts and latency by database
    - d1StorageAdaptiveGroups: row counts and storage size
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    query = """
    query GetD1Usage($accountTag: string!,
                     $datetimeStart: string!,
                     $datetimeEnd: string!) {
      viewer {
        accounts(filter: {accountTag: $accountTag}) {
          d1QueriesAdaptiveGroups(
            limit: 10000
            filter: {datetime_geq: $datetimeStart,
                     datetime_leq: $datetimeEnd}
          ) {
            count
            dimensions { databaseId datetime }
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
        return {"total_queries": 0, "by_database": []}

    accounts = data.get("viewer", {}).get("accounts", [])
    if not accounts:
        return {"total_queries": 0, "by_database": []}

    query_rows = accounts[0].get("d1QueriesAdaptiveGroups", [])
    by_db: dict[str, dict] = {}
    total_queries = 0

    for row in query_rows:
        dims = row.get("dimensions", {})
        count = row.get("count", 0) or 0
        db = dims.get("databaseId", "unknown")

        total_queries += count

        if db not in by_db:
            by_db[db] = {
                "database": db, "queries": 0,
            }
        by_db[db]["queries"] += count

    return {
        "total_queries": total_queries,
        "by_database": sorted(
            by_db.values(),
            key=lambda x: x["queries"],
            reverse=True,
        ),
    }


async def get_kv_usage(days: int = 7) -> dict[str, Any]:
    """Fetch KV operations and storage metrics.

    Datasets:
    - kvOperationsAdaptiveGroups: read/write/delete/list counts
    - kvStorageAdaptiveGroups: stored keys and bytes
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    query = """
    query GetKVUsage($accountTag: string!,
                     $datetimeStart: string!,
                     $datetimeEnd: string!) {
      viewer {
        accounts(filter: {accountTag: $accountTag}) {
          kvOperationsAdaptiveGroups(
            limit: 10000
            filter: {datetime_geq: $datetimeStart,
                     datetime_leq: $datetimeEnd}
          ) {
            sum { requests }
            dimensions { actionType }
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
        return {"total_operations": 0, "by_action": []}

    accounts = data.get("viewer", {}).get("accounts", [])
    if not accounts:
        return {"total_operations": 0, "by_action": []}

    op_rows = accounts[0].get("kvOperationsAdaptiveGroups", [])

    by_action: dict[str, dict] = {}
    total_operations = 0

    for row in op_rows:
        dims = row.get("dimensions", {})
        sums = row.get("sum", {})
        action = dims.get("actionType", "unknown")
        req = sums.get("requests", 0) or 0

        total_operations += req

        if action not in by_action:
            by_action[action] = {"action": action, "requests": 0}
        by_action[action]["requests"] += req

    return {
        "total_operations": total_operations,
        "by_action": sorted(
            by_action.values(),
            key=lambda x: x["requests"],
            reverse=True,
        ),
    }


async def get_vectorize_usage(days: int = 7) -> dict[str, Any]:
    """Fetch Vectorize vector index metrics.

    Dataset: vectorizeQueriesAdaptiveGroups (if available).
    Falls back to empty if the dataset is not yet provisioned.
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=days)
    query = """
    query GetVectorizeUsage($accountTag: string!,
                             $datetimeStart: string!,
                             $datetimeEnd: string!) {
      viewer {
        accounts(filter: {accountTag: $accountTag}) {
          vectorizeQueriesAdaptiveGroups(
            limit: 10000
            filter: {datetime_geq: $datetimeStart,
                     datetime_leq: $datetimeEnd}
          ) {
            dimensions { indexName datetime }
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
            "total_queries": 0, "total_vectors_queried": 0,
            "total_vectors_inserted": 0, "by_index": [],
        }

    accounts = data.get("viewer", {}).get("accounts", [])
    if not accounts:
        return {
            "total_queries": 0, "total_vectors_queried": 0,
            "total_vectors_inserted": 0, "by_index": [],
        }

    rows = accounts[0].get("vectorizeQueriesAdaptiveGroups", [])
    by_index: dict[str, dict] = {}
    total_queries = 0
    total_vectors_queried = 0
    total_vectors_inserted = 0

    for row in rows:
        dims = row.get("dimensions", {})
        sums = row.get("sum", {})
        vq = sums.get("vectorsQueried", 0) or 0
        vi = sums.get("vectorsInserted", 0) or 0
        index = dims.get("indexName", "unknown")

        total_queries += vq + vi
        total_vectors_queried += vq
        total_vectors_inserted += vi

        if index not in by_index:
            by_index[index] = {
                "index": index, "queries": 0,
                "vectors_queried": 0, "vectors_inserted": 0,
            }
        by_index[index]["queries"] += vq + vi
        by_index[index]["vectors_queried"] += vq
        by_index[index]["vectors_inserted"] += vi

    return {
        "total_queries": total_queries,
        "total_vectors_queried": total_vectors_queried,
        "total_vectors_inserted": total_vectors_inserted,
        "by_index": sorted(
            by_index.values(),
            key=lambda x: x["queries"],
            reverse=True,
        ),
    }


async def get_cf_overview(days: int = 7) -> dict[str, Any]:
    """Fetch a combined Cloudflare analytics overview.

    Aggregates Workers AI, Workers invocations, R2, D1, KV,
    and Vectorize metrics into a single response for dashboards.
    Queries run concurrently; individual dataset failures return empty
    sections rather than failing the whole overview.
    """
    ai, workers, r2, d1, kv, vectorize = await asyncio.gather(
        get_workers_ai_usage(days),
        get_workers_invocations(days),
        get_r2_usage(days),
        get_d1_usage(days),
        get_kv_usage(days),
        get_vectorize_usage(days),
    )

    free_ai_limit = 10000 * days
    return {
        "workers_ai": {
            "total_requests": ai["total_requests"],
            "total_neurons": ai["total_neurons"],
            "free_tier_limit": free_ai_limit,
            "free_tier_remaining": max(
                0, free_ai_limit - ai["total_neurons"]
            ),
            "by_model": ai["by_model"],
            "by_day": ai["by_day"],
        },
        "workers": {
            "total_requests": workers["total_requests"],
            "total_errors": workers["total_errors"],
            "by_script": workers["by_script"],
        },
        "r2": {
            "total_operations": r2["total_operations"],
            "total_storage_bytes": r2["total_storage_bytes"],
            "by_bucket": r2["by_bucket"],
            "by_action": r2["by_action"],
        },
        "d1": {
            "total_queries": d1["total_queries"],
            "by_database": d1["by_database"],
        },
        "kv": {
            "total_operations": kv["total_operations"],
            "by_action": kv["by_action"],
        },
        "vectorize": {
            "total_queries": vectorize["total_queries"],
            "total_vectors_queried": vectorize["total_vectors_queried"],
            "total_vectors_inserted": vectorize["total_vectors_inserted"],
            "by_index": vectorize["by_index"],
        },
    }

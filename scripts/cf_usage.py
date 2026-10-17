#!/usr/bin/env python3
"""Cloudflare infrastructure usage report via the free GraphQL Analytics API.

Reads CLOUDFLARE_API_TOKEN + CLOUDFLARE_ACCOUNT_ID from the repo-root .env
(or the environment) and reports the last N days of usage for:

  Workers        workersInvocationsAdaptive     (requests, errors, cpuTimeP50)
  R2             r2OperationsAdaptiveGroups     (ops by bucket + action)
  D1             d1AnalyticsAdaptiveGroups      (queries, rows read/written)
  KV             kvOperationsAdaptiveGroups     (reads/writes by namespace)
  Workers AI     aiInferenceAdaptiveGroups      (inference count by model)
  AI Gateway     aiGatewayRequestsAdaptiveGroups (requests + token sums)
  Vectorize      REST /vectorize/v2/indexes     (no GraphQL dataset exists)

Usage:
    python3 scripts/cf_usage.py              # last 7 days
    python3 scripts/cf_usage.py --days 1     # last 24h
    python3 scripts/cf_usage.py --worker mail-ingest --status  # error deep-dive

Free-tier reference (workers free plan): 100k worker req/day, 10k Workers AI
neurons/day, D1 5M rows read + 100k rows written/day, R2 10GB + 1M ClassA +
10M ClassB ops/month, KV 100k reads + 1k writes/day, Vectorize 5M queried
vector dims/month.

Exit codes: 0 ok / 1 api or config error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GQL = "https://api.cloudflare.com/client/v4/graphql"
REST = "https://api.cloudflare.com/client/v4"


def load_env() -> tuple[str, str]:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith("CLOUDFLARE_API_TOKEN="):
            os.environ.setdefault("CLOUDFLARE_API_TOKEN", line.split("=", 1)[1])
        if line.startswith("CLOUDFLARE_ACCOUNT_ID="):
            os.environ.setdefault("CLOUDFLARE_ACCOUNT_ID", line.split("=", 1)[1])
    token, acct = os.getenv("CLOUDFLARE_API_TOKEN"), os.getenv("CLOUDFLARE_ACCOUNT_ID")
    if not token or not acct:
        sys.exit("missing CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID")
    return token, acct


def gql(token: str, acct: str, body: str) -> dict:
    query = f"{{viewer{{accounts(filter:{{accountTag:\"{acct}\"}}){{{body}}}}}}}"
    req = urllib.request.Request(
        GQL,
        data=json.dumps({"query": query}).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        sys.exit(f"GraphQL HTTP {e.code}: {e.read()[:200]}")
    if resp.get("errors"):
        sys.exit("GraphQL: " + "; ".join(e.get("message", "?") for e in resp["errors"]))
    return resp["data"]["viewer"]["accounts"][0]


def rest(token: str, acct: str, path: str) -> list[dict]:
    req = urllib.request.Request(
        f"{REST}/accounts/{acct}/{path}", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        return json.loads(urllib.request.urlopen(req, timeout=30).read()).get("result") or []
    except urllib.error.HTTPError as e:
        print(f"  REST {path}: HTTP {e.code}")
        return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--worker", help="single-worker deep dive")
    ap.add_argument("--status", action="store_true", help="break worker out by invocation status")
    args = ap.parse_args()
    token, acct = load_env()

    now = datetime.now(timezone.utc)
    dt_start = (now - timedelta(days=args.days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d_start = (now - timedelta(days=args.days)).strftime("%Y-%m-%d")
    d_end = now.strftime("%Y-%m-%d")

    workers_filter = f'datetime_geq:"{dt_start}"'
    if args.worker:
        workers_filter += f', scriptName:"{args.worker}"'
    workers_dims = "scriptName status" if args.status else "scriptName"

    data = gql(token, acct, f"""
      workers: workersInvocationsAdaptive(limit:100, filter:{{{workers_filter}}},
          orderBy:[sum_requests_DESC]){{
        sum{{ requests errors }} dimensions{{ {workers_dims} }} }}
      r2: r2OperationsAdaptiveGroups(limit:200, filter:{{date_geq:"{d_start}",
          date_leq:"{d_end}"}}, orderBy:[sum_requests_DESC]){{
        sum{{ requests }} dimensions{{ bucketName actionType }} }}
      d1: d1AnalyticsAdaptiveGroups(limit:100, filter:{{date_geq:"{d_start}",
          date_leq:"{d_end}"}}, orderBy:[sum_rowsRead_DESC]){{
        sum{{ readQueries writeQueries rowsRead rowsWritten }} dimensions{{ databaseId }} }}
      kv: kvOperationsAdaptiveGroups(limit:100, filter:{{date_geq:"{d_start}",
          date_leq:"{d_end}"}}, orderBy:[sum_requests_DESC]){{
        sum{{ requests }} dimensions{{ namespaceId actionType }} }}
      ai: aiInferenceAdaptiveGroups(limit:100, filter:{{datetime_geq:"{dt_start}"}},
          orderBy:[count_DESC]){{ count dimensions{{ modelId }} }}
      aigw: aiGatewayRequestsAdaptiveGroups(limit:100,
          filter:{{datetime_geq:"{dt_start}"}}, orderBy:[count_DESC]){{
        count sum{{ uncachedTokensIn uncachedTokensOut }} dimensions{{ gateway provider }} }}
    """)

    d1_names = {d["uuid"]: d["name"] for d in rest(token, acct, "d1/database")}
    kv_names = {n["id"]: n["title"] for n in rest(token, acct, "storage/kv/namespaces")}

    print(f"=== Cloudflare usage — last {args.days}d ({d_start} .. {d_end}) ===\n")

    print("Workers:")
    for r in data.get("workers") or []:
        dim, s = r["dimensions"], r["sum"]
        tag = f" {dim['scriptName']}"
        if args.status:
            tag += f" [{dim['status']}]"
        print(f"  {tag:52} req={s['requests']:>7}  err={s['errors']}")

    print("\nR2 ops by bucket:")
    by_bucket: dict[str, dict] = {}
    for r in data.get("r2") or []:
        b = by_bucket.setdefault(r["dimensions"]["bucketName"], {"a": 0, "b": 0})
        cls = "a" if r["dimensions"]["actionType"].startswith(("Put", "List", "Create", "Complete", "Abort", "Delete")) else "b"
        b[cls] += r["sum"]["requests"]
    for name, b in sorted(by_bucket.items(), key=lambda kv: -(kv[1]["a"] + kv[1]["b"])):
        print(f"  {name:32} classA={b['a']:>7}  classB={b['b']:>7}")

    print("\nD1:")
    for r in data.get("d1") or []:
        s = r["sum"]
        name = d1_names.get(r["dimensions"]["databaseId"], r["dimensions"]["databaseId"][:8])
        print(f"  {name:22} readQ={s['readQueries']:>8} rowsRead={s['rowsRead']:>9} writeQ={s['writeQueries']:>6} rowsWritten={s['rowsWritten']:>6}")

    print("\nKV:")
    for r in data.get("kv") or []:
        name = kv_names.get(r["dimensions"]["namespaceId"], r["dimensions"]["namespaceId"][:8])
        print(f"  {name:26} {r['dimensions']['actionType']:6} {r['sum']['requests']:>6}")

    print("\nWorkers AI inferences:")
    for r in data.get("ai") or []:
        print(f"  {r['dimensions']['modelId']:48} {r['count']:>6}")
    if not (data.get("ai") or []):
        print("  none")

    print("\nAI Gateway:")
    for r in data.get("aigw") or []:
        s = r["sum"]
        print(f"  {r['dimensions']['gateway']}/{r['dimensions']['provider']}: {r['count']} req, tokIn={s['uncachedTokensIn']} tokOut={s['uncachedTokensOut']}")
    if not (data.get("aigw") or []):
        print("  none")

    print("\nVectorize indexes:")
    idxs = rest(token, acct, "vectorize/v2/indexes")
    for i in idxs:
        print(f"  {i.get('name'):30} dims={i.get('config', {}).get('dimensions')} created={str(i.get('created_on'))[:10]}")
    if not idxs:
        print("  none")
    return 0


if __name__ == "__main__":
    sys.exit(main())

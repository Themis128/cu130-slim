#!/usr/bin/env python3
"""Create (or inspect) the zone Logpush job streaming cloudless.gr
``http_requests`` logs into the datalake R2 bucket.

As of 2026-09 Logpush is self-service on all plans with 25 GB/month included
for internal (R2) destinations, but the account flag must be enabled on the
account — if the API answers ``logpush is not enabled for this account``
(1004), open the dashboard Logpush page once
(https://dash.cloudflare.com/?to=/:account/logs) or retry later
the rollout
is staged. All tokens needed live in the repo-root ``.env``:

    CLOUDFLARE_API_TOKEN   needs Zone -> Logs -> Edit on cloudless.gr
    R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY   R2 API token for the bucket
    CLOUDFLARE_ACCOUNT_ID  account the bucket lives in

Usage:
    python3 scripts/cf_logpush_job.py            # create (idempotent: skips
                                               # an existing http_requests job)
    python3 scripts/cf_logpush_job.py --list     # list existing zone jobs
    python3 scripts/cf_logpush_job.py --off      # disable the job

Logs land under ``lake/../cloudflare-http-logs/{DATE}`` in datalake-bucket,
fields chosen for funnel joins (host/URI/status/cache/WAF/ray-id).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

ZONE_NAME = "cloudless.gr"
DATASET = "http_requests"
BUCKET = "datalake-bucket"
PATH_PREFIX = "cloudflare-http-logs"

FIELD_NAMES = [
    "ClientIP",
    "ClientRequestHost",
    "ClientRequestMethod",
    "ClientRequestURI",
    "ClientRequestReferer",
    "ClientRequestUserAgent",
    "EdgeResponseStatus",
    "EdgeResponseBytes",
    "EdgeCacheStatus",
    "WAFAction",
    "RayID",
    "EdgeStartTimestamp",
    "EdgeEndTimestamp",
]


def _env() -> dict[str, str]:
    env = dict(os.environ)
    for candidate in (".env", os.path.join(os.path.dirname(__file__), "..", ".env")):
        try:
            with open(candidate) as fh:
                for line in fh:
                    line = line.strip()
                    if line and "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        env.setdefault(k.strip(), v.strip())
        except OSError:
            continue
    return env


def _api(env: dict[str, str], method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {env['CLOUDFLARE_API_TOKEN']}",
            "Content-Type": "application/json",
        },
        data=json.dumps(body).encode() if body is not None else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return json.loads(e.read() or b"{}")


def _zone_id(env: dict[str, str]) -> str:
    r = _api(env, "GET", f"/zones?name={ZONE_NAME}")
    zones = r.get("result") or []
    if not zones:
        sys.exit(f"zone {ZONE_NAME} not found: {r.get('errors')}")
    return zones[0]["id"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true", help="list existing jobs")
    ap.add_argument("--off", action="store_true", help="disable the job")
    args = ap.parse_args()

    env = _env()
    for key in ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
                "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        if not env.get(key):
            sys.exit(f"missing {key} in env/.env")

    zone = _zone_id(env)
    jobs = (_api(env, "GET", f"/zones/{zone}/logpush/jobs").get("result") or [])

    if args.list:
        for j in jobs:
            print(j["id"], j.get("dataset"), "enabled" if j.get("enabled") else "disabled",
                  j.get("name"))
        return 0

    job = next((j for j in jobs if j.get("dataset") == DATASET), None)
    if args.off:
        if not job:
            print("no http_requests job to disable")
            return 0
        r = _api(env, "PUT", f"/zones/{zone}/logpush/jobs/{job['id']}",
                 {"enabled": False})
        print("disabled" if r.get("success") else r.get("errors"))
        return 0 if r.get("success") else 1

    if job:
        print(f"job already exists: id={job['id']} enabled={job.get('enabled')}")
        return 0

    dest = (
        f"r2://{BUCKET}/{PATH_PREFIX}/{{DATE}}"
        f"?account-id={env['CLOUDFLARE_ACCOUNT_ID']}"
        f"&access-key-id={env['R2_ACCESS_KEY_ID']}"
        f"&secret-access-key={env['R2_SECRET_ACCESS_KEY']}"
    )
    r = _api(env, "POST", f"/zones/{zone}/logpush/jobs", {
        "name": f"{ZONE_NAME} {DATASET} to datalake",
        "dataset": DATASET,
        "enabled": True,
        "destination_conf": dest,
        "output_options": {
            "field_names": FIELD_NAMES,
            "timestamp_format": "rfc3339",
        },
    })
    if not r.get("success"):
        print("create failed:", [e.get("message") for e in r.get("errors", [])])
        return 1
    print(f"created job id={r['result']['id']} -> {BUCKET}/{PATH_PREFIX}/{{DATE}}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""cf_logpush_setup.py — create the workers-logs Logpush job -> R2.

Logpush jobs are CF-API-only (no wrangler command). Needs a token with
*Logpush Edit* + *R2 Edit* — wrangler OAuth can't cover this, so the env
token must be a real API token.

Steps: check token -> ensure R2 S3 creds -> create logpush job (dataset
`workers_trace_events` -> `cloudless-datalake/worker-logs/`) -> verify.

Env (from .env, never printed):
  CLOUDFLARE_API_TOKEN / CF_API_TOKEN — Logpush Edit + R2 Edit
  CLOUDFLARE_ACCOUNT_ID — defaults to fb7dc7b69b662480cd5961a4d1913c78
  R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY — S3 creds for destination_conf
    (script can also try `wrangler r2 bucket` auto-create path note)

Usage: python backend/scripts/cf_logpush_setup.py [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

CF = "https://api.cloudflare.com/client/v4"
env_file = Path(__file__).resolve().parents[3] / ".env"
ACCOUNT_DEFAULT = "fb7dc7b69b662480cd5961a4d1913c78"


def _env(key: str, default: str = "") -> str:
    if val := os.environ.get(key):
        return val.strip()
    try:
        for line in env_file.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return default


def _api(method: str, path: str, token: str, payload: dict | None = None) -> dict:
    req = urllib.request.Request(
        CF + path,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        data=json.dumps(payload).encode() if payload else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"success": False, "errors": [{"message": e.read().decode()[:400]}]}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--json", action="store_true")
    args = p.parse_args()
    token = _env("CLOUDFLARE_API_TOKEN") or _env("CF_API_TOKEN")
    account = _env("CLOUDFLARE_ACCOUNT_ID", ACCOUNT_DEFAULT)
    rows: list[dict] = []

    def say(name, ok, detail, fix=""):
        rows.append({"check": name, "status": "ok" if ok else "action", "detail": detail, "fix": fix})

    if not token:
        say("cf-token", False, "no CLOUDFLARE_API_TOKEN/CF_API_TOKEN in env",
            "dash.cloudflare.com -> My Profile -> API Tokens -> create with "
            "Workers Scripts Edit + Logpush Edit + R2 Edit, add to .env")
        return _report(rows, args.json)

    # verify token + scopes the cheap way: hit a read endpoint
    chk = _api("GET", "/user/tokens/verify", token)
    if not chk.get("success"):
        say("token-verify", False, str(chk.get("errors"))[:200], "token invalid — recreate")
        return _report(rows, args.json)
    say("token-verify", True, "valid")

    key_id, secret = _env("R2_ACCESS_KEY_ID"), _env("R2_SECRET_ACCESS_KEY")
    if key_id and secret:
        say("r2-creds", True, "S3 keys present")
        dest = (
            f"r2://cloudless-datalake/worker-logs/{{DATE}}?account-id={account}"
            f"&access-key-id={key_id}&secret-access-key={secret}"
        )
    else:
        say("r2-creds", True, "no S3 keys - trying managed R2 conf (account-id only)")
        dest = f"r2://cloudless-datalake/worker-logs/{{DATE}}?account-id={account}"
    jobs = _api("GET", f"/accounts/{account}/logpush/jobs", token)
    existing = [j for j in (jobs.get("result") or []) if j.get("dataset") == "workers_trace_events" and j.get("enabled")]
    if existing:
        say("logpush-job", True, f"job {existing[0].get('id')} already enabled")
        return _report(rows, args.json)
    res = _api("POST", f"/accounts/{account}/logpush/jobs", token, {
        "name": "workers-trace-events",
        "dataset": "workers_trace_events",
        "destination_conf": dest,
        "enabled": True,
        "output_options": {"field_names": [], "output_type": "ndjson", "timestamp_format": "rfc3339"},
    })
    if not res.get("success"):
        say("logpush-job", False, str(res.get("errors"))[:300],
            "token lacks Logpush Edit — recreate with that scope")
        return _report(rows, args.json)
    say("logpush-job", True, f"job {res['result'].get('id')} -> r2://cloudless-datalake/worker-logs/")
    return _report(rows, args.json)


def _report(rows: list[dict], as_json: bool) -> int:
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        for r in rows:
            mark = "ok " if r["status"] == "ok" else "FIX"
            print(f"[{mark}] {r['check']:<20} {r['detail']}")
            if r["fix"]:
                print(f"      -> {r['fix']}")
    return 0 if all(r["status"] == "ok" for r in rows) else 1


if __name__ == "__main__":
    sys.exit(main())

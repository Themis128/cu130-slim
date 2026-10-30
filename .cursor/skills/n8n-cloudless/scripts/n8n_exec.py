#!/usr/bin/env python3
"""n8n_exec.py — n8n execution manager that reads the API key from the
running container (immune to .env key drift — .env's N8N_API_KEY was
stale as of 2026-10-30 while the container's own env had the live one).

Usage:
  n8n_exec.py failed [--limit 10]   # recent failed executions
  n8n_exec.py retry <id>            # retry one execution
  n8n_exec.py retry-failed          # retry every failed execution
  n8n_exec.py workflows             # list workflows + active flag
"""
import argparse
import json
import subprocess
import sys
import urllib.parse
import urllib.request

BASE = "http://localhost:5678"


def api_key():
    k = subprocess.run(["docker", "exec", "n8n", "printenv", "N8N_API_KEY"],
                       capture_output=True, text=True).stdout.strip()
    if k:
        return k
    raise SystemExit("couldn't read N8N_API_KEY from the n8n container")


def req(path, method="GET"):
    r = urllib.request.Request(
        BASE + path, method=method,
        headers={"X-N8N-API-KEY": api_key()})
    return json.load(urllib.request.urlopen(r, timeout=20))


def failed(limit=10):
    d = req("/api/v1/executions?" +
            urllib.parse.urlencode({"status": "error", "limit": limit}))
    out = []
    for e in d.get("data", d if isinstance(d, list) else []):
        out.append((e.get("id"), e.get("status"),
                    str(e.get("startedAt"))[:19], e.get("workflowId")))
    return out


def retry(eid):
    try:
        req(f"/api/v1/executions/{eid}/retry", method="POST")
        return "retried"
    except Exception as e:
        return f"retry failed: {str(e)[:80]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["failed", "retry", "retry-failed",
                                    "workflows"])
    ap.add_argument("arg", nargs="?")
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args()

    if a.cmd == "failed":
        for row in failed(a.limit):
            print(*row)
    elif a.cmd == "retry":
        print(retry(a.arg))
    elif a.cmd == "retry-failed":
        for eid, *_ in failed(20):
            print(eid, retry(eid))
    elif a.cmd == "workflows":
        d = req("/api/v1/workflows?limit=50")
        for w in d.get("data", []):
            print(w["id"], w["name"][:55],
                  "active" if w.get("active") else "inactive")


if __name__ == "__main__":
    main()

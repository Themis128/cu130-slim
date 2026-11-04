#!/usr/bin/env python3
"""Probe every MCP server in .devin/mcp_config.json.

For command-based servers: spawn the process, send an MCP `initialize`,
report whether a JSON-RPC result came back. For url-based servers: HTTP GET.

Usage: python mcp_health.py [--json] [--timeout 20]
Exit 0 if all probed servers respond, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CONFIG = REPO / ".devin" / "mcp_config.json"


def probe_command(name: str, spec: dict, timeout: int) -> tuple[str, str]:
    cmd = [spec["command"], *spec.get("args", [])]
    init = json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "mcp_health", "version": "1"}},
    }) + "\n"
    try:
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True)
        out, err = p.communicate(init, timeout=timeout)
        p.kill()
        first = (out.strip().split("\n") or [""])[0]
        if '"result"' in first:
            return "ok", "initialize handshake"
        if '"error"' in first and "method" in first:
            return "ok", "responded (method error — alive)"
        tail = err.strip().split("\n")[-1][:120] if err.strip() else "no output"
        return "fail", tail
    except FileNotFoundError:
        return "fail", f"command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        p.kill()
        return "fail", f"no response in {timeout}s"
    except Exception as e:  # noqa: BLE001
        return "fail", str(e)[:120]


def probe_url(name: str, spec: dict, timeout: int) -> tuple[str, str]:
    url = spec["url"]
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json, text/event-stream"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return "ok" if r.status < 500 else "fail", f"http {r.status}"
    except urllib.error.HTTPError as e:
        # 4xx still proves the endpoint is alive
        return ("ok" if e.code < 500 else "fail"), f"http {e.code}"
    except Exception as e:  # noqa: BLE001
        return "fail", str(e)[:100]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--timeout", type=int, default=20)
    ap.add_argument("--only", help="comma-separated server names")
    args = ap.parse_args()

    servers = json.loads(CONFIG.read_text())["mcpServers"]
    if args.only:
        wanted = set(args.only.split(","))
        servers = {k: v for k, v in servers.items() if k in wanted}

    results = {}
    for name, spec in servers.items():
        if spec.get("url"):
            state, detail = probe_url(name, spec, args.timeout)
        elif spec.get("command"):
            state, detail = probe_command(name, spec, args.timeout)
        else:
            state, detail = "skip", "no command/url"
        results[name] = (state, detail)
        if not args.json:
            print(f"{name:20s} {state:4s} {detail}")

    if args.json:
        print(json.dumps({k: {"state": s, "detail": d} for k, (s, d) in results.items()}, indent=2))
    fails = [k for k, (s, _) in results.items() if s == "fail"]
    if not args.json:
        print(f"\n{len(results) - len(fails)}/{len(results)} healthy; fails: {', '.join(fails) or 'none'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())

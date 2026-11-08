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
import os
import select
import subprocess
import sys
import threading
import time
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
        env = {**os.environ, **spec.get("env", {})}
        p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, bufsize=1, env=env)
        # Drain stderr on a thread so a chatty proxy can't block on a full pipe.
        err_chunks: list[str] = []
        threading.Thread(target=lambda: err_chunks.extend(iter(p.stderr.readline, "")), daemon=True).start()
        # Keep stdin OPEN while reading — MCP clients hold the pipe for the
        # session; closing it makes proxies (mcp-remote) shut down before they
        # can answer initialize.
        p.stdin.write(init)
        p.stdin.flush()
        deadline = time.monotonic() + timeout
        first = ""
        while time.monotonic() < deadline:
            ready, _, _ = select.select([p.stdout], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                break
            line = p.stdout.readline()
            if not line:
                break
            if '"result"' in line or '"error"' in line:
                first = line.strip()
                break
        p.kill()
        try:
            p.wait(timeout=5)
        except Exception:
            pass
        if first:
            if '"result"' in first:
                return "ok", "initialize handshake"
            if '"error"' in first and "method" in first:
                return "ok", "responded (method error — alive)"
        tail = ("".join(err_chunks).strip().split("\n") or ["no output"])[-1][:120]
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

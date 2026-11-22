#!/usr/bin/env python3
"""socialauto_mcp_probe.py — health-check the repo-local MCP servers.

Reads .devin/mcp_config.json, launches each stdio server (or checks each
HTTP endpoint), performs the MCP initialize + tools/list handshake, and
reports which servers answer and how many tools they expose.

Usage:
    socialauto_mcp_probe.py [--all] [name ...]

    --all     probe every server in mcp_config.json (default: just
              'socialauto', the platform server)
    name      one or more server names to probe

Exit code 0 when every probed server answers tools/list, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
CONFIG = REPO / ".devin" / "mcp_config.json"
TIMEOUT = 60


def load_config() -> dict:
    return json.loads(CONFIG.read_text())["mcpServers"]


def probe_http(url: str) -> tuple[bool, str]:
    """Cheap liveness for streamable-HTTP MCP endpoints."""
    try:
        req = urllib.request.Request(url, method="GET")
        urllib.request.urlopen(req, timeout=5)
        return True, "http 200"
    except urllib.error.HTTPError as e:
        # 405/400 still means the endpoint is alive and speaking HTTP
        if e.code in (400, 405, 406, 415):
            return True, f"http {e.code} (endpoint alive)"
        return False, f"http {e.code}"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


def probe_stdio(spec: dict) -> tuple[bool, str]:
    """Spawn the server, run initialize + tools/list over stdio."""
    cmd = [spec["command"], *spec.get("args", [])]
    env = {**os.environ, **spec.get("env", {})}
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            cwd=spec.get("cwd") or str(REPO),
            env=env,
        )
    except FileNotFoundError as e:
        return False, f"spawn failed: {e}"

    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "socialauto-mcp-probe", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        proc.stdin.write("\n".join(json.dumps(m) for m in msgs) + "\n")
        proc.stdin.flush()
        proc.stdin.close()
    except Exception as e:
        proc.kill()
        return False, f"stdin write failed: {e}"

    tools: list[str] = []
    server = "?"
    try:
        import select
        deadline = TIMEOUT
        buf = ""
        while deadline > 0 and proc.poll() is None:
            r, _, _ = select.select([proc.stdout], [], [], 1.0)
            deadline -= 1
            if not r:
                continue
            line = proc.stdout.readline()
            if not line:
                break
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("id") == 1:
                server = d.get("result", {}).get("serverInfo", {}).get("name", "?")
            if d.get("id") == 2:
                tools = [t["name"] for t in d.get("result", {}).get("tools", [])]
                break
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as e:
        proc.kill()
        return False, f"read failed: {e}"

    if not tools and server == "?":
        return False, "no initialize/tools response"
    return True, f"{server}: {len(tools)} tools" + (f" ({', '.join(tools[:6])}{'…' if len(tools) > 6 else ''})" if tools else "")


def probe_tcp(url: str) -> tuple[bool, str]:
    """host:port TCP check for url-style endpoints."""
    from urllib.parse import urlparse
    p = urlparse(url if "://" in url else "http://" + url)
    try:
        socket.create_connection((p.hostname, p.port or 80), timeout=4).close()
        return True, f"tcp {p.hostname}:{p.port or 80} open"
    except Exception as e:
        return False, f"tcp closed: {e}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    cfg = load_config()
    names = list(cfg) if args.all else (args.names or ["socialauto"])

    failures = 0
    for name in names:
        spec = cfg.get(name)
        if spec is None:
            print(f"[MISS] {name} — not in mcp_config.json")
            failures += 1
            continue
        if "url" in spec:
            ok, detail = probe_http(spec["url"]) if spec["url"].startswith("http") else probe_tcp(spec["url"])
        else:
            ok, detail = probe_stdio(spec)
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")
        failures += 0 if ok else 1

    print(f"\n{len(names) - failures}/{len(names)} servers healthy")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

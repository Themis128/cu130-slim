#!/usr/bin/env python3
"""mcp_fleet_doctor.py — diagnose + suggest fixes for the repo MCP fleet.

Runs the stdio/HTTP probe (socialauto_mcp_probe logic inlined) AND checks
the environment failure classes observed 2026-11-22:

  - stack-ops container state (its port publishes are the :9227/:9228
    socat forwards for linkedin/airbyte MCP)
  - .playwright-data/pw-mcp-config.json + init-script.js (gitignored;
    wiped dir = playwright MCP dead)
  - ~/.mcp-auth/<workspace>/ token caches for mcp-remote OAuth servers
  - docker socket / docker-desktop engine reachability

Usage: mcp_fleet_doctor.py [--all] [name ...]
Exit 0 when every probed server is healthy AND no env check is broken.
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
PROBE_TIMEOUT = 75  # mcp-remote warmup is 20-75s


def sh(cmd: list[str], timeout: int = 10) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:
        return 1, str(e)


def check_env() -> list[tuple[str, bool, str]]:
    """Environment checks — (name, ok, detail)."""
    out: list[tuple[str, bool, str]] = []

    # Docker engine reachable
    rc, txt = sh(["docker", "info", "--format", "{{.ServerVersion}}"], 15)
    out.append(("docker-engine", rc == 0, txt.splitlines()[-1][:80] if txt else "no response"))

    # stack-ops (publishes :9227/:9228 socat forwards + control plane)
    rc, txt = sh(["docker", "inspect", "-f", "{{.State.Status}}", "stack-ops"], 10)
    status = txt.strip() if rc == 0 else "missing"
    out.append(("stack-ops", status == "running", f"container {status}"))
    if status == "running":
        for port, svc in ((9227, "linkedin-mcp"), (9228, "airbyte-mcp")):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=3).close()
                out.append((f"fwd:{port}", True, f"{svc} forward open"))
            except Exception:
                out.append((f"fwd:{port}", False, f"{svc} forward closed — stack-ops socat dead?"))

    # playwright-data files (gitignored — wipe = dead MCP)
    pd = REPO / ".playwright-data"
    for f in ("pw-mcp-config.json", "init-script.js"):
        ok = (pd / f).is_file()
        out.append((f"playwright-data/{f}", ok,
                    "present" if ok else f"MISSING — restore: git show a94ff269f:.playwright-data/{f} > .playwright-data/{f}"))

    # mcp-remote OAuth caches
    auth = Path.home() / ".mcp-auth"
    workspaces = [d.name for d in auth.iterdir() if d.is_dir()] if auth.is_dir() else []
    out.append(("mcp-auth-caches", bool(workspaces),
                f"{len(workspaces)} workspace caches: {', '.join(workspaces) or 'NONE — OAuth servers will demand interactive login'}"))

    # key containers for stdio servers
    for c in ("social-api", "n8n", "metabase", "nextcloud-mcp-server", "linkedin-mcp-server", "airbyte-mcp-server"):
        rc, txt = sh(["docker", "inspect", "-f", "{{.State.Status}}", c], 10)
        st = txt.strip() if rc == 0 else "missing"
        out.append((f"container:{c}", st == "running", st))

    return out


def probe_stdio(spec: dict) -> tuple[bool, str]:
    import select
    cmd = [spec["command"], *spec.get("args", [])]
    env = {**os.environ, **spec.get("env", {})}
    try:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True,
            cwd=spec.get("cwd") or str(REPO), env=env,
        )
    except FileNotFoundError as e:
        return False, f"spawn failed: {e}"

    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                    "clientInfo": {"name": "mcp-fleet-doctor", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    try:
        # stdin stays OPEN — mcp-remote exits on EOF before answering
        proc.stdin.write("\n".join(json.dumps(m) for m in msgs) + "\n")
        proc.stdin.flush()
    except Exception as e:
        proc.kill()
        return False, f"stdin write failed: {e}"

    tools: list[str] = []
    server = "?"
    deadline = PROBE_TIMEOUT
    try:
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

    if server == "?" and not tools:
        return False, "no initialize/tools response"
    return True, f"{server}: {len(tools)} tools"


def probe(spec: dict) -> tuple[bool, str]:
    if "url" in spec:
        url = spec["url"]
        if url.startswith("http"):
            try:
                urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=5)
                return True, "http alive"
            except urllib.error.HTTPError as e:
                if e.code in (400, 405, 406, 415):
                    return True, f"http {e.code} (endpoint alive)"
                return False, f"http {e.code}"
            except Exception as e:
                return False, f"{type(e).__name__}: {e}"
        # host:port
        from urllib.parse import urlparse
        p = urlparse("http://" + url)
        try:
            socket.create_connection((p.hostname, p.port or 80), timeout=4).close()
            return True, f"tcp {p.hostname}:{p.port or 80} open"
        except Exception as e:
            return False, f"tcp closed: {e}"
    return probe_stdio(spec)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--env-only", action="store_true", help="skip MCP probes, env checks only")
    args = ap.parse_args()

    failures = 0

    if not args.env_only:
        cfg = json.loads(CONFIG.read_text())["mcpServers"]
        names = list(cfg) if args.all else (args.names or ["socialauto"])
        for name in names:
            spec = cfg.get(name)
            if spec is None:
                print(f"[MISS] {name}")
                failures += 1
                continue
            ok, detail = probe(spec)
            print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")
            failures += 0 if ok else 1

    print("\n── environment ──")
    env_fail = 0
    for name, ok, detail in check_env():
        print(f"[{'OK' if ok else 'FAIL'}] {name}: {detail}")
        env_fail += 0 if ok else 1

    total = failures + env_fail
    print(f"\n{'ALL HEALTHY' if total == 0 else f'{total} issue(s)'}")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Shared helpers for docker-model-runner scripts."""

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

DMR_BASE = env("DMR_BASE") or "http://localhost:12435"
CHAT_BASE = f"{DMR_BASE}/engines/llama.cpp/v1"

# DMR's port 12435 is published on the Docker Desktop host network only —
# unreachable from WSL (localhost, gateway and nameserver all refuse,
# verified 2026-11-12). Inside backend containers it answers on
# host.docker.internal, so fall back to a `docker exec` HTTP call.
DMR_CONTAINER_BASE = env("DMR_CONTAINER_BASE") or "http://host.docker.internal:12435"
DMR_CONTAINERS = [
    c for c in (env("DMR_CONTAINER") or "social-api", "social-worker-default", "social-api") if c
]

_EXEC_SNIPPET = r"""
import json, sys, urllib.request
req = json.load(sys.stdin)
data = json.dumps(req["body"]).encode() if req["body"] is not None else None
r = urllib.request.Request(
    req["url"], data=data,
    headers={"Content-Type": "application/json"}, method=req["method"])
try:
    with urllib.request.urlopen(r, timeout=req.get("timeout", 30)) as resp:
        sys.stdout.write(resp.read().decode())
except Exception as exc:
    sys.stderr.write(repr(exc))
    sys.exit(1)
"""


def _via_container(method: str, path: str, body: dict | None, timeout: int) -> str | None:
    """Issue the DMR HTTP call from inside a backend container."""
    url = DMR_CONTAINER_BASE + path
    payload = json.dumps({"method": method, "url": url, "body": body, "timeout": timeout}).encode()
    for container in dict.fromkeys(DMR_CONTAINERS):
        try:
            p = subprocess.run(
                ["docker", "exec", "-i", container, "python", "-c", _EXEC_SNIPPET],
                input=payload, capture_output=True, timeout=timeout + 15,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if p.returncode == 0 and p.stdout:
            return p.stdout.decode()
    return None


def dmr_get(path: str, timeout: int = 15) -> str | None:
    try:
        with urllib.request.urlopen(f"{DMR_BASE}{path}", timeout=timeout) as r:
            return r.read().decode()
    except Exception:
        return _via_container("GET", path, None, timeout)


def dmr_post(path: str, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        f"{DMR_BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        raw = _via_container("POST", path, body, timeout)
        if raw is None:
            return {"error": "unreachable (direct + container fallback)"}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"raw": raw}


def docker_model(*args: str) -> int:
    return subprocess.run(["docker", "model", *args]).returncode


def dmr_online() -> bool:
    return dmr_get("/engines/v1/models") is not None

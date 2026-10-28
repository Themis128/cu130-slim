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


def dmr_get(path: str) -> str | None:
    try:
        with urllib.request.urlopen(f"{DMR_BASE}{path}", timeout=15) as r:
            return r.read().decode()
    except urllib.error.URLError:
        return None


def dmr_post(path: str, body: dict, timeout: int = 120) -> dict:
    req = urllib.request.Request(
        f"{DMR_BASE}{path}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except urllib.error.URLError as e:
        return {"error": str(e)}


def docker_model(*args: str) -> int:
    return subprocess.run(["docker", "model", *args]).returncode


def dmr_online() -> bool:
    return dmr_get("/engines/v1/models") is not None

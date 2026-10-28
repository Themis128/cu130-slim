#!/usr/bin/env python3
"""Shared helpers for tiktok-console-ops scripts."""

import json
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, repo_root  # noqa: E402

ROOT = repo_root()
OUT_DIR = ROOT / ".cursor/tmp-tiktok-pw/out"
NODE_WORK = ROOT / ".cursor/tmp-tiktok-pw/node_work"
PW_IMAGE = "mcr.microsoft.com/playwright:v1.62.1"
SIDECAR = env("TIKTOK_SIDECAR_URL") or "http://127.0.0.1:9224"

EXPECTED_SCOPES = (
    "user.info.basic,user.info.profile,user.info.stats,"
    "video.list,video.publish,video.upload")
EXPECTED_REDIRECT = "https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback"


def env_key(key: str) -> str:
    for line in (ROOT / ".env").read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].rstrip("\r").strip('"').strip("'")
    return ""


def ensure_playwright() -> None:
    NODE_WORK.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not (NODE_WORK / "node_modules/playwright").exists():
        subprocess.run([
            "docker", "run", "--rm", "-v", f"{NODE_WORK}:/work", "-w", "/work",
            PW_IMAGE, "bash", "-lc",
            "npm init -y >/dev/null && npm i playwright@1.62.1 --no-fund --no-audit"],
            check=True)


def run_mjs(script: str, extra_args: list[str] | None = None,
            extra_env: dict | None = None) -> int:
    """Run a .mjs Playwright script inside the Playwright docker image."""
    target = NODE_WORK / script
    src = Path(__file__).resolve().parent / "lib" / script
    if not src.exists():
        src = Path(__file__).resolve().parent / script
    import shutil
    shutil.copy(src, target)

    e = dict(os.environ)
    e.update({
        "TIKTOK_DEV_EMAIL": env_key("TIKTOK_DEV_EMAIL"),
        "TIKTOK_DEV_PASSWORD": env_key("TIKTOK_DEV_PASSWORD"),
        "OUT_DIR": "/out",
        "PLAYWRIGHT_BROWSERS_PATH": "/ms-playwright",
    })
    if extra_env:
        e.update(extra_env)

    cmd = ["docker", "run", "--rm", "--network", "host"]
    for k in ("TIKTOK_DEV_EMAIL", "TIKTOK_DEV_PASSWORD", "OUT_DIR",
              "PLAYWRIGHT_BROWSERS_PATH", *(extra_env or {})):
        cmd += ["-e", k]
    cmd += ["-v", f"{NODE_WORK}:/work", "-v", f"{OUT_DIR}:/out", "-w", "/work",
            PW_IMAGE, "node", f"/work/{script}"]
    cmd += extra_args or []
    return subprocess.run(cmd, env=e).returncode


def cf_request(path: str, method: str = "GET", data: dict | None = None) -> dict:
    token = env_key("CLOUDFLARE_API_TOKEN") or env_key("CLOUDFLARE_DNS_API_TOKEN")
    if not token:
        return {"success": False, "errors": ["No CLOUDFLARE_API_TOKEN"]}
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4{path}", data=body, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def cf_zone_id() -> str:
    z = cf_request("/zones?name=cloudless.gr")
    if not z.get("success") or not z.get("result"):
        return ""
    return z["result"][0]["id"]


def tiktok_txt_records(zone_id: str) -> list[dict]:
    data = cf_request(f"/zones/{zone_id}/dns_records?type=TXT&per_page=100")
    rows = []
    for r in data.get("result", []):
        c = r.get("content", "")
        if "tiktok" not in c.lower():
            continue
        kind = ("domain" if "tiktok-domain-verification=" in c else
                "site" if "tiktok-developers-site-verification=" in c else "other")
        shown = c if len(c) < 48 else c[:36] + "…" + c[-8:]
        rows.append({"id": r["id"], "name": r["name"], "kind": kind, "content": shown})
    return rows

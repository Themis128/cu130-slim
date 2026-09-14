#!/usr/bin/env python3
"""TikTok Console MCP Server (stdio JSON-RPC).

Wraps `.cursor/skills/tiktok-console-ops/scripts/*` for agent use:
config checks, sidecar session, Cloudflare DNS TXT, developer-console
inspect/domain-verify, SocialAuto API smoke, docs checklist.

Never prints secrets (passwords, client secrets, full session cookies).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(os.environ.get(
    "CU130_ROOT",
    Path(__file__).resolve().parents[4],  # .../cu130-slim
))
SCRIPTS = ROOT / ".cursor" / "skills" / "tiktok-console-ops" / "scripts"
ENV_PATH = ROOT / ".env"

TOOLS: list[dict[str, Any]] = [
    {
        "name": "tiktok_check_config",
        "description": "Compare SocialAuto .env + sidecar + DNS against official TikTok Login Kit / Content Posting expectations.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tiktok_sidecar_status",
        "description": "GET TikTok browser sidecar health and session status (port 9224).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tiktok_sidecar_ensure_session",
        "description": "Playwright Docker login to TikTok.com and inject sessionid into the browser sidecar. Uses TIKTOK_DEV_* from .env.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tiktok_dns_list",
        "description": "List Cloudflare TXT records on cloudless.gr related to TikTok (site vs domain verification).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tiktok_dns_add_domain_txt",
        "description": "Add tiktok-domain-verification=… TXT at @ on cloudless.gr via Cloudflare API.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "token": {
                    "type": "string",
                    "description": "Full string starting with tiktok-domain-verification=",
                },
            },
            "required": ["token"],
        },
    },
    {
        "name": "tiktok_console_inspect",
        "description": "Login to developers.tiktok.com and dump Cloudless app state (approval, redirect drift, domain token hints). Does not submit audit.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tiktok_domain_verify",
        "description": "Full official domain-verify flow: console token → Cloudflare TXT → click Verify. Long-running.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tiktok_api_smoke",
        "description": "Smoke SocialAuto TikTok management endpoints for the connected account (health, creator-info).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "tiktok_docs_checklist",
        "description": "Return the official-docs configuration checklist and current local status summary.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


def _text_result(text: str, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _error_result(msg: str) -> dict[str, Any]:
    return _text_result(msg, is_error=True)


def _run_script(name: str, args: list[str] | None = None, timeout: int = 600) -> dict[str, Any]:
    script = SCRIPTS / name
    if not script.exists():
        return _error_result(f"Script missing: {script}")
    cmd = ["bash", str(script), *(args or [])]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "CU130_ROOT": str(ROOT)},
        )
    except subprocess.TimeoutExpired:
        return _error_result(f"Timeout running {name}")
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
    # Redact obvious secrets
    for key in ("TIKTOK_CLIENT_SECRET", "TIKTOK_DEV_PASSWORD", "session_id", "sessionid"):
        if key.lower() in out.lower() and "PASSWORD" in key:
            out = out  # scripts already avoid printing passwords
    return _text_result(out[-12000:] if len(out) > 12000 else out, is_error=proc.returncode != 0)


def _load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    if not ENV_PATH.exists():
        return env
    for line in ENV_PATH.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _http_json(url: str, method: str = "GET", data: dict | None = None, headers: dict | None = None) -> Any:
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def _admin_token() -> str:
    env = _load_env()
    email = env.get("SOCIAL_ADMIN_EMAIL", "")
    password = env.get("SOCIAL_ADMIN_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("SOCIAL_ADMIN_EMAIL/PASSWORD missing")
    form = urllib.parse.urlencode({"username": email, "password": password}).encode()
    req = urllib.request.Request(
        "http://127.0.0.1:8083/api/v1/auth/login",
        data=form,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())["access_token"]


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "tiktok_check_config":
        return _run_script("check-config.sh", timeout=120)

    if name == "tiktok_sidecar_status":
        return _run_script("sidecar-session.sh", ["status"], timeout=60)

    if name == "tiktok_sidecar_ensure_session":
        return _run_script("sidecar-session.sh", ["ensure"], timeout=300)

    if name == "tiktok_dns_list":
        return _run_script("dns-tiktok-txt.sh", ["list"], timeout=60)

    if name == "tiktok_dns_add_domain_txt":
        token = args.get("token", "")
        if not str(token).startswith("tiktok-domain-verification="):
            return _error_result("token must start with tiktok-domain-verification=")
        return _run_script("dns-tiktok-txt.sh", ["add", str(token)], timeout=60)

    if name == "tiktok_console_inspect":
        return _run_script("console-inspect.sh", timeout=300)

    if name == "tiktok_domain_verify":
        return _run_script("domain-verify.sh", timeout=600)

    if name == "tiktok_api_smoke":
        try:
            token = _admin_token()
            headers = {"Authorization": f"Bearer {token}"}
            # find tiktok account
            accounts = _http_json(
                "http://127.0.0.1:8083/api/v1/accounts",
                headers=headers,
            )
            if isinstance(accounts, dict):
                accounts = accounts.get("items") or accounts.get("accounts") or []
            tt = next(
                (a for a in accounts if str(a.get("platform", "")).lower() == "tiktok"),
                None,
            )
            if not tt:
                return _error_result("No TikTok account connected")
            aid = tt["id"]
            health = _http_json(
                f"http://127.0.0.1:8083/api/v1/tiktok/accounts/{aid}/health",
                headers=headers,
            )
            creator = _http_json(
                f"http://127.0.0.1:8083/api/v1/tiktok/accounts/{aid}/creator-info",
                headers=headers,
            )
            return _text_result(json.dumps({
                "account_id": aid,
                "username": tt.get("username") or tt.get("display_name"),
                "health": health,
                "creator_info": creator,
            }, indent=2))
        except Exception as e:
            return _error_result(str(e))

    if name == "tiktok_docs_checklist":
        env = _load_env()
        checklist = {
            "docs": [
                "https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide/#pull_from_url",
                "https://developers.tiktok.com/doc/content-posting-api-get-started",
                "https://developers.tiktok.com/doc/login-kit-web",
            ],
            "required": {
                "client_key_secret": bool(env.get("TIKTOK_CLIENT_KEY") and env.get("TIKTOK_CLIENT_SECRET")),
                "redirect_https_gr": env.get("TIKTOK_REDIRECT_URI")
                == "https://social.cloudless.gr/api/v1/auth/oauth/tiktok/callback",
                "scopes": "user.info.basic,user.info.profile,video.list,video.publish,video.upload",
                "domain_txt_tiktok_domain_verification": "required for PULL_FROM_URL / photos",
                "publish_mode_pre_audit": "MEDIA_UPLOAD",
                "direct_post": "only after app audit approved",
                "sidecar_session": "required for browser privacy APIs",
            },
            "console_drift_to_fix": [
                "Web URL must be https://social.cloudless.gr (not .jp)",
                "Login Kit redirect must match TIKTOK_REDIRECT_URI exactly",
                "Verify domains under Content Posting API",
            ],
            "scripts": str(SCRIPTS),
        }
        return _text_result(json.dumps(checklist, indent=2))

    return _error_result(f"Unknown tool: {name}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            response: dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "tiktok-console-mcp", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            result = handle_tool_call(params.get("name", ""), params.get("arguments") or {})
            response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        else:
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""ntfy MCP Server.

Stdlib-only MCP (JSON-RPC over stdio) server wrapping the self-hosted ntfy
instance — the same ops surface as ntfy_ops.py plus message publish/read.

Read/publish goes over the public HTTPS endpoint with basic-auth credentials
(the `pc` user is the canonical read/publish identity); user administration
(users/grant/token/passwd) goes over `ssh github-omv` -> k3s kubectl exec,
which only works from the workstation WSL where ~/.ssh/omv_id_rsa exists.

Config (env vars):
  NTFY_BASE_URL  - default https://ntfy.cloudless.gr
  NTFY_USER      - basic-auth user for read/publish (default: pc)
  NTFY_PASSWORD  - basic-auth password; falls back to repo .env NTFY_PC_PASSWORD
  NTFY_TOKEN     - bearer token alternative (wins over basic auth when set)
  NTFY_SSH_HOST  - admin lane host, default github-omv

Tools exposed (8):
  ntfy_health            - /v1/health + subscriber count from /metrics
  ntfy_publish           - publish a message to a topic (title/priority/tags)
  ntfy_read              - read recent messages from a topic (JSON poll)
  ntfy_users             - list users + ACLs (admin lane)
  ntfy_grant             - grant a user ro/wo/rw access to a topic
  ntfy_token             - mint a non-expiring bearer token for a user
  ntfy_passwd            - rotate a user's password (value arg or generated)
  ntfy_app_setup_help    - the Windows-app subscription gotchas (client-side)

NOTE: the ntfy web/desktop app keeps its subscription list in browser-local
storage — there is NO server-side subscription registry, so no tool here can
pre-populate the user's app. ntfy_app_setup_help returns the exact manual/
GUI-drive recipe.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE = os.environ.get("NTFY_BASE_URL", "https://ntfy.cloudless.gr").rstrip("/")
SSH_HOST = os.environ.get("NTFY_SSH_HOST", "github-omv")
NS = "ntfy"
REPO_ROOT = Path(__file__).resolve().parents[4]
REPO_ENV = REPO_ROOT / ".env"
TIMEOUT = 15


def _env_fallback(key: str) -> str | None:
    if REPO_ENV.is_file():
        for line in REPO_ENV.read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def _creds() -> tuple[str | None, str | None]:
    return (
        os.environ.get("NTFY_USER", "pc") or None,
        os.environ.get("NTFY_PASSWORD") or _env_fallback("NTFY_PC_PASSWORD"),
    )


def _req(method: str, path: str, data: bytes | None = None,
         headers: dict | None = None, raw: bool = False):
    url = path if path.startswith("http") else f"{BASE}{path}"
    req = urllib.request.Request(url, data=data, method=method)
    # Cloudflare blocks Python-urllib/* with HTTP 403/1010 — always set a UA
    req.add_header("User-Agent", "ntfy-mcp-server/1.0 (+https://cloudless.gr)")
    token = os.environ.get("NTFY_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    else:
        user, pw = _creds()
        if user and pw:
            req.add_header(
                "Authorization",
                "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            body = resp.read().decode()
            return body if raw else (json.loads(body) if body.strip() else {"ok": True})
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}: {e.read().decode()[:200]}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _ntfy_cli(*args: str, env: dict | None = None) -> dict:
    prefix = ""
    if env:
        prefix = "env " + " ".join(f"{k}={v}" for k, v in env.items()) + " "
    cmd = (
        f"ssh -i ~/.ssh/omv_id_rsa -o BatchMode=yes -o StrictHostKeyChecking=no "
        f"-o ConnectTimeout=10 {SSH_HOST} "
        f"\"sudo k3s kubectl -n {NS} exec -i deploy/ntfy -- {prefix}ntfy {' '.join(args)}\""
    )
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        return {"error": "ssh not found — admin lane needs the workstation WSL"}
    except subprocess.TimeoutExpired:
        return {"error": "admin lane timed out"}
    if r.returncode != 0:
        return {"error": r.stderr.strip()[:400] or f"exit {r.returncode}"}
    return {"output": r.stdout.strip()}


def _text(s: str) -> dict:
    return {"content": [{"type": "text", "text": s}]}


def _err(s: str) -> dict:
    return {"content": [{"type": "text", "text": f"ERROR: {s}"}], "isError": True}


APP_HELP = """ntfy Windows/desktop app setup (verified 2026-11-26):

1. The app's subscription list lives in browser-local storage — there is NO
   server-side subscription registry. No API/MCP/tool can pre-populate it.
   This is a structural limit of ntfy, not missing tooling.
2. Manual recipe (per topic): app sidebar -> "Subscribe to topic" -> type the
   topic name -> SUBSCRIBE. First protected topic prompts for login once:
   user `pc`, password in repo .env NTFY_PC_PASSWORD. The app caches auth.
3. Subscribing requires read access on the topic — grant via ntfy_grant or
   `ntfy access <user> <topic> rw` on the admin lane.
4. GUI automation CAN drive it (verified): raise+maximize the window, click
   sidebar item, SendKeys the topic, click SUBSCRIBE — see
   devin-scripts/subpass.ps1/subnext.ps1 on the workstation. It only works
   while the window is foregrounded and the user is hands-off — synthetic
   input cannot steal focus from a window the user is typing in.
5. Chrome PWA note: a Chrome "Save password?" popup may appear after login;
   dismiss it — the app caches the session itself.
"""


def handle_tool_call(name: str, args: dict[str, Any]) -> dict:
    if name == "ntfy_health":
        h = _req("GET", "/v1/health")
        m = _req("GET", "/metrics", raw=True)
        subs = ""
        if isinstance(m, str):
            for line in m.splitlines():
                if line.startswith("ntfy_subscribers_total"):
                    subs = line.split()[-1]
        return _text(f"health: {h}\nsubscribers: {subs or 'n/a'}")

    if name == "ntfy_publish":
        topic = args.get("topic", "")
        message = args.get("message", "")
        if not topic or not message:
            return _err("topic and message required")
        headers = {}
        if args.get("title"):
            headers["Title"] = str(args["title"])
        if args.get("priority"):
            headers["Priority"] = str(args["priority"])
        if args.get("tags"):
            headers["Tags"] = str(args["tags"])
        r = _req("POST", f"/{topic}", data=message.encode(), headers=headers)
        if isinstance(r, dict) and "error" in r:
            return _err(r["error"])
        return _text(f"published to {topic}: {json.dumps(r)[:200]}")

    if name == "ntfy_read":
        topic = args.get("topic", "")
        if not topic:
            return _err("topic required")
        since = str(args.get("since", "30m"))
        r = _req("GET", f"/{topic}/json?poll=1&since={since}", raw=True)
        if isinstance(r, dict) and "error" in r:
            return _err(r["error"])
        msgs = []
        for line in (r or "").splitlines():
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if d.get("event") == "message":
                msgs.append(d)
        out = [
            {
                "time": m.get("time"),
                "title": m.get("title", ""),
                "priority": m.get("priority"),
                "message": (m.get("message") or "")[:500],
            }
            for m in msgs[-int(args.get("limit", 20)):]
        ]
        return _text(json.dumps(out, indent=1) if out else "(no messages)")

    if name == "ntfy_users":
        r = _ntfy_cli("user", "list")
        return _text(r.get("output", "")) if "output" in r else _err(r["error"])

    if name == "ntfy_grant":
        user, topic, perm = args.get("user"), args.get("topic"), args.get("permission", "rw")
        if not user or not topic or perm not in ("ro", "wo", "rw"):
            return _err("user, topic and permission(ro|wo|rw) required")
        r = _ntfy_cli("access", user, topic, perm)
        return _text(r.get("output", "granted")) if "output" in r else _err(r["error"])

    if name == "ntfy_token":
        user = args.get("user")
        if not user:
            return _err("user required")
        r = _ntfy_cli("token", "add", user)
        return _text(r.get("output", "")) if "output" in r else _err(r["error"])

    if name == "ntfy_passwd":
        user = args.get("user")
        if not user:
            return _err("user required")
        pw = args.get("password") or ("ntfy-" + secrets.token_urlsafe(12))
        r = _ntfy_cli("user", "change-pass", user, env={"NTFY_PASSWORD": pw})
        if "output" in r:
            return _text(f"password rotated for {user}: {pw}\n{r['output']}")
        return _err(r["error"])

    if name == "ntfy_app_setup_help":
        return _text(APP_HELP)

    return _err(f"unknown tool: {name}")


TOOLS = [
    {
        "name": "ntfy_health",
        "description": "Check ntfy server health + live subscriber count.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ntfy_publish",
        "description": "Publish a message to an ntfy topic (title/priority/tags optional).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "message": {"type": "string"},
                "title": {"type": "string"},
                "priority": {"type": "integer", "description": "1-5"},
                "tags": {"type": "string", "description": "comma-separated"},
            },
            "required": ["topic", "message"],
        },
    },
    {
        "name": "ntfy_read",
        "description": "Read recent messages from a topic.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "since": {"type": "string", "description": "e.g. 30m, 2h, 1d"},
                "limit": {"type": "integer", "description": "max messages (default 20)"},
            },
            "required": ["topic"],
        },
    },
    {
        "name": "ntfy_users",
        "description": "List ntfy users and their ACLs (admin lane via ssh github-omv).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "ntfy_grant",
        "description": "Grant a user access to a topic (ro/wo/rw).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "topic": {"type": "string"},
                "permission": {"type": "string", "enum": ["ro", "wo", "rw"]},
            },
            "required": ["user", "topic"],
        },
    },
    {
        "name": "ntfy_token",
        "description": "Mint a non-expiring bearer token for a user.",
        "inputSchema": {
            "type": "object",
            "properties": {"user": {"type": "string"}},
            "required": ["user"],
        },
    },
    {
        "name": "ntfy_passwd",
        "description": "Rotate a user's password (returns the new value).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user": {"type": "string"},
                "password": {"type": "string", "description": "optional; generated if omitted"},
            },
            "required": ["user"],
        },
    },
    {
        "name": "ntfy_app_setup_help",
        "description": "Explain the Windows/desktop app subscription recipe and its structural limits.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]


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
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "ntfy-mcp-server", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            result = handle_tool_call(params.get("name", ""), params.get("arguments", {}))
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

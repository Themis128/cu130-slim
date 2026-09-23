#!/usr/bin/env python3
"""Cloudflare token-ops MCP server.

Exposes Cloudflare API-token management and Access service-token operations as
MCP tools over stdio. Pure stdlib, JSON-RPC line protocol (same pattern as
n8n-mcp-server.py).

Auth model (resolved from env or repo-root .env):
  CLOUDFLARE_GLOBAL_KEY + CLOUDFLARE_EMAIL  — Global API Key; the ONLY credential
      that can create/edit API tokens (Cloudflare forbids token-managed tokens).
  CLOUDFLARE_ACCESS_TOKEN                   — account token 'cloudless-access';
      can list/manage Access apps and service tokens, cannot manage API tokens.
  CLOUDFLARE_ACCOUNT_ID                     — defaults to fb7dc7b6… (cloudless.gr)

Secret hygiene: created token values / service-token secrets are written to
/tmp/cf-token-*.json (0600); tool results return the file path only.

Tools:
  cf_verify                - which credential mode is active + token status
  cf_list_tokens           - user + account API tokens (name/id/status)
  cf_list_perm_groups      - permission groups for user|account scope
  cf_add_token_perm        - add a permission group to an existing token
  cf_create_token          - create a user|account API token
  cf_list_service_tokens   - Access service tokens on the account
  cf_create_service_token  - create an Access service token
  cf_list_access_apps      - Access applications (name/domain/decisions)
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[4]
REPO_ENV = REPO_ROOT / ".env"
ACCOUNT = os.environ.get("CLOUDFLARE_ACCOUNT_ID",
                         "fb7dc7b69b662480cd5961a4d1913c78")
API = "https://api.cloudflare.com/client/v4"


def _env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if v:
        return v
    if REPO_ENV.is_file():
        for line in REPO_ENV.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _global_auth() -> bool:
    return bool(_env("CLOUDFLARE_GLOBAL_KEY") and _env("CLOUDFLARE_EMAIL"))


def _headers() -> dict[str, str]:
    if _global_auth():
        return {"X-Auth-Key": _env("CLOUDFLARE_GLOBAL_KEY"),
                "X-Auth-Email": _env("CLOUDFLARE_EMAIL"),
                "Content-Type": "application/json"}
    return {"Authorization": f"Bearer {_env('CLOUDFLARE_ACCESS_TOKEN')}",
            "Content-Type": "application/json"}


def _cf(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    req = urllib.request.Request(API + path, method=method, headers=_headers(),
                                 data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"success": False, "errors": [{"message": e.read().decode()[:300]}]}
    except Exception as e:
        return {"success": False, "errors": [{"message": str(e)}]}


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error: {msg}"}], "isError": True}


def _res(d: dict) -> dict[str, Any]:
    if not d.get("success"):
        return _err(json.dumps(d.get("errors", d))[:600])
    return _text(json.dumps(d["result"], indent=2, default=str))


def _need_global() -> str | None:
    if not _global_auth():
        return ("needs CLOUDFLARE_GLOBAL_KEY + CLOUDFLARE_EMAIL in .env — "
                "API tokens cannot manage tokens. Get it: dash.cloudflare.com "
                "→ My Profile → API Tokens → Global API Key → View.")
    return None


def _find_pg(scope: str, substr: str) -> dict | str:
    path = ("/user/tokens/permission_groups" if scope == "user"
            else f"/accounts/{ACCOUNT}/tokens/permission_groups")
    d = _cf("GET", path)
    if not d.get("success"):
        return f"perm-groups error: {d.get('errors')}"
    m = [g for g in d["result"] if substr.lower() in g.get("name", "").lower()]
    if not m:
        return f"no {scope} permission group matching {substr!r}"
    if len(m) > 1:
        return ("ambiguous — matches: " +
                "; ".join(f"{g['name']} ({g['id']})" for g in m))
    return m[0]


def _policy(scope: str, pg: dict) -> dict:
    res = ({f"com.cloudflare.api.account.{ACCOUNT}": "*"} if scope == "account"
           else {"com.cloudflare.api.account.*": "*"})
    return {"effect": "allow", "permission_groups": [{"id": pg["id"]}],
            "resources": res}


def _stash(name: str, data: dict) -> str:
    out = Path(f"/tmp/cf-token-{name}.json")
    out.write_text(json.dumps(data, indent=2))
    out.chmod(0o600)
    return str(out)


TOOLS = [
    {"name": "cf_verify",
     "description": "Show which CF credential mode is active (global-key vs scoped-token) and verify token status.",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "cf_list_tokens",
     "description": "List all API tokens (user + account scoped): name, id, status. Never returns token values.",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "cf_list_perm_groups",
     "description": "List Cloudflare permission groups for a scope. Filter by name substring.",
     "inputSchema": {"type": "object", "properties": {
         "scope": {"type": "string", "enum": ["user", "account"], "default": "account"},
         "filter": {"type": "string", "description": "Name substring filter"}}, "required": []}},
    {"name": "cf_add_token_perm",
     "description": "Add a permission group to an existing API token. Requires global key.",
     "inputSchema": {"type": "object", "properties": {
         "token_id": {"type": "string"},
         "perm": {"type": "string", "description": "Permission-group name substring, e.g. 'Access: Service Tokens'"}},
         "required": ["token_id", "perm"]}},
    {"name": "cf_create_token",
     "description": "Create an API token (user or account scope) with named permission groups. Requires global key. Value is written to a 0600 file, not returned.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "scope": {"type": "string", "enum": ["user", "account"], "default": "account"},
         "perms": {"type": "array", "items": {"type": "string"},
                   "description": "Permission-group name substrings"}},
         "required": ["name", "perms"]}},
    {"name": "cf_list_service_tokens",
     "description": "List Cloudflare Access service tokens on the account.",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
    {"name": "cf_create_service_token",
     "description": "Create a Cloudflare Access service token. Needs global key OR an access token with 'Access: Service Tokens'. Secret is written to a 0600 file, not returned.",
     "inputSchema": {"type": "object", "properties": {
         "name": {"type": "string"},
         "duration": {"type": "string", "default": "forever",
                     "description": "e.g. 'forever', '8760h'"}},
         "required": ["name"]}},
    {"name": "cf_list_access_apps",
     "description": "List Cloudflare Access applications: name, domain, policy decisions.",
     "inputSchema": {"type": "object", "properties": {}, "required": []}},
]


def handle(name: str, args: dict) -> dict[str, Any]:
    if name == "cf_verify":
        mode = "global-key (full)" if _global_auth() else "scoped-token (limited)"
        lines = [f"auth mode: {mode}"]
        for label, path in (("user", "/user/tokens/verify"),
                            ("account", f"/accounts/{ACCOUNT}/tokens/verify")):
            r = _cf("GET", path)
            if r.get("success"):
                t = r["result"] or {}
                lines.append(f"{label} verify: {t.get('status')} id={t.get('id')}")
        if _global_auth():
            me = _cf("GET", "/user")
            if me.get("success"):
                lines.append(f"user: {me['result'].get('email')}")
        return _text("\n".join(lines))

    if name == "cf_list_tokens":
        out = []
        if _global_auth():
            d = _cf("GET", "/user/tokens?per_page=100")
            if d.get("success"):
                out.append("── user tokens ──")
                out += [f"{t['id']}  {t['name']}  {t.get('status')}"
                        for t in d["result"]]
        d = _cf("GET", f"/accounts/{ACCOUNT}/tokens?per_page=100")
        if not d.get("success"):
            return _err(str(d.get("errors")))
        out.append("── account tokens ──")
        out += [f"{t['id']}  {t['name']}  {t.get('status')}" for t in d["result"]]
        return _text("\n".join(out))

    if name == "cf_list_perm_groups":
        scope = args.get("scope", "account")
        path = ("/user/tokens/permission_groups" if scope == "user"
                else f"/accounts/{ACCOUNT}/tokens/permission_groups")
        d = _cf("GET", path)
        if not d.get("success"):
            return _err(str(d.get("errors")))
        flt = args.get("filter", "").lower()
        rows = [f"{g['id']}  {g.get('name')}" for g in d["result"]
                if not flt or flt in g.get("name", "").lower()]
        return _text("\n".join(rows) or "no matches")

    if name == "cf_add_token_perm":
        if e := _need_global():
            return _err(e)
        tid, substr = args["token_id"], args["perm"]
        base, scope = f"/accounts/{ACCOUNT}/tokens/{tid}", "account"
        d = _cf("GET", base)
        if not d.get("success"):
            base, scope = f"/user/tokens/{tid}", "user"
            d = _cf("GET", base)
        if not d.get("success"):
            return _err(f"token {tid} not found")
        tok = d["result"]
        pg = _find_pg(scope, substr)
        if isinstance(pg, str):
            return _err(pg)
        policies = tok.get("policies", [])
        merged = False
        for p in policies:
            if p.get("effect") == "allow" and p.get("resources"):
                ids = {g["id"] for g in p.get("permission_groups", [])}
                if pg["id"] not in ids:
                    p.setdefault("permission_groups", []).append({"id": pg["id"]})
                merged = True
                break
        if not merged:
            policies.append(_policy(scope, pg))
        r = _cf("PUT", base, {"name": tok["name"],
                              "status": tok.get("status", "active"),
                              "policies": policies})
        if not r.get("success"):
            return _err(str(r.get("errors")))
        return _text(f"OK — {tok['name']} now has '{pg['name']}'")

    if name == "cf_create_token":
        if e := _need_global():
            return _err(e)
        name_, scope = args["name"], args.get("scope", "account")
        policies = []
        for s in args["perms"]:
            pg = _find_pg(scope, s)
            if isinstance(pg, str):
                return _err(pg)
            policies.append(_policy(scope, pg))
        path = "/user/tokens" if scope == "user" else f"/accounts/{ACCOUNT}/tokens"
        r = _cf("POST", path, {"name": name_, "policies": policies})
        if not r.get("success"):
            return _err(str(r.get("errors")))
        t = r["result"]
        f = _stash(name_, {"id": t["id"], "name": t["name"], "value": t["value"]})
        return _text(f"created {t['name']} id={t['id']} → value in {f} (0600)")

    if name == "cf_list_service_tokens":
        d = _cf("GET", f"/accounts/{ACCOUNT}/access/service_tokens")
        if not d.get("success"):
            return _err(str(d.get("errors")))
        rows = [f"{t['id']}  {t.get('name')}  expires={t.get('expires_at')}"
                for t in d["result"]]
        return _text("\n".join(rows) or "no service tokens in this account")

    if name == "cf_create_service_token":
        r = _cf("POST", f"/accounts/{ACCOUNT}/access/service_tokens",
                {"name": args["name"], "duration": args.get("duration", "forever")})
        if not r.get("success"):
            return _err(str(r.get("errors")) +
                        " — token lacks 'Access: Service Tokens' write; "
                        "grant it via cf_add_token_perm (needs global key) or "
                        "create the token in the dashboard")
        t = r["result"]
        f = _stash(f"svc-{args['name']}", {"id": t["id"], "name": t["name"],
                                         "client_id": t["client_id"],
                                         "client_secret": t["client_secret"]})
        return _text(f"created service token {t['name']} id={t['id']} → {f} (0600)")

    if name == "cf_list_access_apps":
        d = _cf("GET", f"/accounts/{ACCOUNT}/access/apps?per_page=100")
        if not d.get("success"):
            return _err(str(d.get("errors")))
        rows = []
        for a in d["result"]:
            dec = ",".join(p.get("decision", "?") for p in a.get("policies", []))
            rows.append(f"{a['id']}  {a.get('domain', '')}  {a.get('name')}  [{dec}]")
        return _text("\n".join(rows) or "no apps")

    return _err(f"Unknown tool: {name}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method, msg_id = msg.get("method", ""), msg.get("id")
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "cloudflare-token-ops", "version": "1.0.0"}}}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}
        elif method == "tools/call":
            resp = {"jsonrpc": "2.0", "id": msg_id,
                    "result": handle(msg.get("params", {}).get("name", ""),
                                     msg.get("params", {}).get("arguments", {}))}
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            resp = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        else:
            resp = {"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""platform-ops MCP Server.

Read-only operational surface for cu130-slim services that have no other MCP
coverage: Postgres (social-postgres:5433), Redis, Celery via Flower (:5555),
Chroma (:8001), ComfyUI (:8000), Prometheus metrics (:9390), and compose
service state. Complements the socialauto MCP (app API) — this one covers the
infrastructure layer.

stdio MCP server, Python stdlib only. Service access via `docker exec` for
psql/redis-cli, urllib for HTTP. Credentials are read from the repo .env and
never returned in results.

Config:
{
  "mcpServers": {
    "platform": {
      "command": "python3",
      "args": [".../social-stack-ops/scripts/platform-mcp-server.py"],
      "cwd": "/home/tbaltzakis/cu130-slim"
    }
  }
}

Tools (8, all read-only):
  stack_ps         — docker compose ps (name/service/status)
  service_health   — probe every platform endpoint (api, n8n, sidecars, ...)
  pg_query         — SELECT-only SQL on social-postgres
                     (databases: social_automation|n8n|social_automation_test)
  redis_inspect    — redis INFO/DBSIZE/GET/TTL/TYPE/KEYS/SLOWLOG
  celery_status    — Flower API: workers|tasks|types|broker
  chroma_stats     — heartbeat + collections list
  comfyui_status   — /queue + /system_stats (VRAM under-reports on WSL2)
  metrics_digest   — parse :9390/metrics, optional substring filter
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

REPO = os.environ.get("CU130_ROOT", "/home/tbaltzakis/cu130-slim")
ENV_FILE = os.path.join(REPO, ".env")

_env_cache: dict | None = None


def _env() -> dict:
    global _env_cache
    if _env_cache is None:
        _env_cache = {}
        try:
            with open(ENV_FILE) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        _env_cache[k.strip()] = v.strip()
        except OSError:
            pass
    return _env_cache


def _http(url: str, timeout: int = 8, basic: str | None = None):
    """GET -> (status|None, body). 401/405 still prove the listener is up."""
    headers = {"Accept": "application/json"}
    if basic:
        headers["Authorization"] = "Basic " + base64.b64encode(
            basic.encode()).decode()
    try:
        with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers),
                timeout=timeout) as r:
            return r.status, r.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")[:500]
    except Exception as e:  # noqa: BLE001 — any failure = unreachable
        return None, str(e)


def _docker_exec(container: str, args: list[str], timeout: int = 20):
    try:
        r = subprocess.run(["docker", "exec", container] + args,
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:  # noqa: BLE001
        return 1, "", str(e)


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def _stack_ps(_a: dict) -> dict[str, Any]:
    r = subprocess.run(
        ["docker", "compose", "ps", "-a",
         "--format", "{{.Name}}\t{{.Service}}\t{{.Status}}"],
        capture_output=True, text=True, cwd=REPO, timeout=25)
    if r.returncode != 0:
        return {"error": r.stderr.strip() or "compose ps failed"}
    rows = []
    for line in r.stdout.splitlines():
        name, _, rest = line.partition("\t")
        svc, _, status = rest.partition("\t")
        rows.append({"name": name, "service": svc, "status": status})
    bad = [r2 for r2 in rows
           if "healthy" not in r2["status"] and "Up" not in r2["status"]]
    return {"count": len(rows), "unhealthy": bad, "services": rows}


_PROBES = {
    "social-api":        "http://127.0.0.1:8083/api/v1/health",
    "social-frontend":   "http://127.0.0.1:8082/",
    "n8n":               "http://127.0.0.1:5678/healthz",
    "airbyte-mcp":       "http://127.0.0.1:9229/health",
    "linkedin-mcp":      "http://127.0.0.1:9227/mcp",
    "chroma":            "http://127.0.0.1:8001/api/v2/heartbeat",
    "comfyui":           "http://127.0.0.1:8000/system_stats",
    "flower":            "http://127.0.0.1:5555/api/workers",
    "metrics":           "http://127.0.0.1:9390/metrics",
    "browser-novnc":     "http://127.0.0.1:9223/health",
    "linkedin-sidecar":  "http://127.0.0.1:9225/health",
    "tiktok-sidecar":    "http://127.0.0.1:9224/health",
    "facebook-sidecar":  "http://127.0.0.1:9226/health",
    "messenger-sidecar": "http://127.0.0.1:9230/health",
}


# stack-ops fronts these containers with a wake-on-connect proxy — probing
# their port wakes them. Map probe name -> stack-ops container name; when
# stack-ops reports "stopped" the service is sleeping-on-demand, not down.
_STACKOPS_PROXIED = {
    "browser-novnc": "browser-novnc",
    "linkedin-sidecar": "linkedin-browser-sidecar",
    "tiktok-sidecar": "tiktok-browser-sidecar",
    "facebook-sidecar": "facebook-browser-sidecar",
    "messenger-sidecar": "messenger-sidecar",
    "linkedin-mcp": "linkedin-mcp-server",
    "airbyte-mcp": "airbyte-mcp-server",
    "flower": "flower",
    "comfyui": "social-media-comfyui-gpu",
}
_STACK_OPS_STATUS = "http://127.0.0.1:8787/status"


def _stack_ops_states() -> dict:
    code, body = _http(_STACK_OPS_STATUS, timeout=5)
    if code != 200 or not body:
        return {}
    try:
        return json.loads(body)
    except Exception:
        return {}


def _service_health(_a: dict) -> dict[str, Any]:
    states = _stack_ops_states()

    def _state_of(container: str | None) -> str | None:
        if not container:
            return None
        if container in states:
            return states[container].get("state")
        # status is keyed by service name; match by container too
        for info in states.values():
            if info.get("container") == container:
                return info.get("state")
        return None

    out = {}
    for name, url in _PROBES.items():
        state = _state_of(_STACKOPS_PROXIED.get(name))
        if state in ("stopped", "missing"):
            out[name] = {"up": True, "http": None,
                         "detail": "sleeping (stack-ops wakes on demand)"}
            continue
        code, body = _http(url, timeout=5)
        up = code is not None  # any HTTP response (even 401/405) = listening
        out[name] = {"up": up, "http": code,
                     "detail": "" if up else body[:120]}
    return out


_FORBIDDEN_SQL = re.compile(
    r"\b(insert|update|delete|drop|alter|truncate|grant|revoke|create|copy|"
    r"execute|call|set|vacuum|refresh|listen|notify|unlisten|"
    r"pg_terminate|pg_cancel|lo_|dblink)\b", re.I)
_PG_DBS = ("social_automation", "n8n", "social_automation_test")


def _pg_query(a: dict) -> dict[str, Any]:
    sql = (a.get("sql") or "").strip().rstrip(";")
    db = a.get("database", "social_automation")
    if db not in _PG_DBS:
        return {"error": f"database '{db}' not allowed {list(_PG_DBS)}"}
    if not re.match(r"^(select|with|explain|show)\b", sql, re.I):
        return {"error": "read-only: must start with SELECT/WITH/EXPLAIN/SHOW"}
    if _FORBIDDEN_SQL.search(sql):
        return {"error": "read-only: forbidden keyword in query"}
    limit = min(int(a.get("limit", 500)), 1000)
    if re.match(r"^select\b", sql, re.I) and " limit " not in sql.lower():
        sql = f"SELECT * FROM ({sql}) _q LIMIT {limit}"
    e = _env()
    rc, out, err = _docker_exec(
        "social-postgres",
        ["env", f"PGPASSWORD={e.get('SOCIAL_POSTGRES_PASSWORD', '')}",
         "psql", "-U", e.get("SOCIAL_POSTGRES_USER", "postgres"),
         "-d", db, "-At", "-F", "\t",
         "-c", "set statement_timeout='15s'", "-c", sql], timeout=25)
    if rc != 0:
        return {"error": err or out or f"psql rc={rc}"}
    lines = out.splitlines()
    rows = lines[1:] if lines and lines[0] == "SET" else lines
    return {"database": db, "rows": len(rows), "result": rows[:limit]}


def _redis_inspect(a: dict) -> dict[str, Any]:
    e = _env()
    cmd = (a.get("command") or "info").lower()
    cli = ["redis-cli", "--no-auth-warning", "-a", e.get("REDIS_PASSWORD", "")]
    if cmd == "info":
        args = cli + ["info", a.get("section") or "server"]
    elif cmd == "dbsize":
        args = cli + ["dbsize"]
    elif cmd in ("get", "ttl", "type", "strlen"):
        key = a.get("key") or ""
        if not key or "\n" in key:
            return {"error": "key required"}
        args = cli + [cmd.upper(), key]
    elif cmd == "scan":
        pat = a.get("pattern") or ""
        if not pat or any(c in pat for c in "\r\n"):
            return {"error": "pattern required"}
        args = cli + ["--scan", "--pattern", pat]
    elif cmd == "slowlog":
        args = cli + ["slowlog", "get", "10"]
    else:
        return {"error": f"'{cmd}' not allowed "
                         "(info|dbsize|get|ttl|type|strlen|scan|slowlog)"}
    rc, out, err = _docker_exec("redis", args, timeout=15)
    if rc != 0:
        return {"error": err or out or f"rc={rc}"}
    return {"result": out[:8000]}


def _celery(a: dict) -> dict[str, Any]:
    e = _env()
    basic = (e.get("FLOWER_BASIC_AUTH")
             or f"{e.get('ENV_MANAGER_USER', '')}:{e.get('ENV_MANAGER_PASS', '')}")
    basic = basic if ":" in basic else ""
    ep = (a.get("endpoint") or "workers").lower()
    paths = {"workers": "/api/workers?refresh=true&status=true",
             "tasks": "/api/tasks?limit=20",
             "types": "/api/task/types", "broker": "/api/broker"}
    if ep not in paths:
        return {"error": f"endpoint '{ep}' not in {list(paths)}"}
    code, body = _http("http://127.0.0.1:5555" + paths[ep], basic=basic)
    if code != 200:
        return {"error": f"flower http {code}", "body": body[:300]}
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"raw": body[:4000]}


def _chroma(_a: dict) -> dict[str, Any]:
    code, hb = _http("http://127.0.0.1:8001/api/v2/heartbeat")
    out: dict[str, Any] = {"heartbeat": hb.strip() if code == 200
                           else f"http {code}"}
    code, body = _http(
        "http://127.0.0.1:8001/api/v2/tenants/default_tenant/"
        "databases/default_database/collections")
    if code == 200:
        try:
            cols = json.loads(body)
            cols = cols if isinstance(cols, list) else cols.get(
                "collections", [])
            out["collections"] = [{"name": c.get("name"), "id": c.get("id")}
                                  for c in cols]
        except json.JSONDecodeError:
            out["collections_raw"] = body[:2000]
    else:
        out["collections_error"] = f"http {code}: {body[:200]}"
    return out


def _comfyui(_a: dict) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ep in ("queue", "system_stats"):
        code, body = _http(f"http://127.0.0.1:8000/{ep}")
        try:
            out[ep] = json.loads(body) if code == 200 else f"http {code}"
        except json.JSONDecodeError:
            out[ep] = body[:500]
    out["note"] = ("VRAM under-reports on WSL2 — real usage is inside the "
                   "docker-model-runner container (docker model ps / "
                   "dmr tools)")
    return out


def _metrics(a: dict) -> dict[str, Any]:
    filt = (a.get("filter") or "").lower()
    code, body = _http("http://127.0.0.1:9390/metrics")
    if code != 200:
        return {"error": f"http {code}", "body": body[:300]}
    lines = [ln for ln in body.splitlines() if ln and not ln.startswith("#")]
    if filt:
        lines = [ln for ln in lines if filt in ln.lower()]
    return {"series": len(lines), "sample": lines[:200]}


_HANDLERS = {
    "stack_ps": _stack_ps, "service_health": _service_health,
    "pg_query": _pg_query, "redis_inspect": _redis_inspect,
    "celery_status": _celery, "chroma_stats": _chroma,
    "comfyui_status": _comfyui, "metrics_digest": _metrics,
}

# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {"name": "stack_ps",
     "description": "docker compose ps — every service, status, unhealthy list.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "service_health",
     "description": "Probe every platform HTTP endpoint (api, n8n, chroma, "
                    "comfyui, flower, sidecars, mcp services). up = listener "
                    "responds (any status code).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "pg_query",
     "description": "Read-only SQL on social-postgres:5433. Only "
                    "SELECT/WITH/EXPLAIN/SHOW; mutating keywords rejected; "
                    "15s statement timeout; default LIMIT 500.",
     "inputSchema": {"type": "object", "properties": {
         "sql": {"type": "string"},
         "database": {"type": "string", "default": "social_automation",
                      "enum": list(_PG_DBS)},
         "limit": {"type": "integer", "default": 500}},
         "required": ["sql"]}},
    {"name": "redis_inspect",
     "description": "Redis read-only: info(section), dbsize, get/ttl/type/"
                    "strlen(key), scan(pattern), slowlog.",
     "inputSchema": {"type": "object", "properties": {
         "command": {"type": "string", "default": "info",
                     "enum": ["info", "dbsize", "get", "ttl", "type",
                              "strlen", "scan", "slowlog"]},
         "section": {"type": "string"},
         "key": {"type": "string"},
         "pattern": {"type": "string"}}}},
    {"name": "celery_status",
     "description": "Celery via Flower: workers|tasks|types|broker.",
     "inputSchema": {"type": "object", "properties": {
         "endpoint": {"type": "string", "default": "workers",
                      "enum": ["workers", "tasks", "types", "broker"]}}}},
    {"name": "chroma_stats",
     "description": "Chroma v2 heartbeat + collection names/ids.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "comfyui_status",
     "description": "ComfyUI /queue + /system_stats. NOTE: VRAM under-reports "
                    "on WSL2 — use dmr tools for real GPU state.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "metrics_digest",
     "description": "Parse :9390/metrics; optional substring filter.",
     "inputSchema": {"type": "object", "properties": {
         "filter": {"type": "string"}}}},
]


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error: {msg}"}],
            "isError": True}


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    fn = _HANDLERS.get(name)
    if not fn:
        return _err(f"unknown tool {name}")
    try:
        result = fn(args)
    except Exception as e:  # noqa: BLE001 — report, don't crash the loop
        return _err(f"{type(e).__name__}: {e}")
    if isinstance(result, dict) and "error" in result:
        return _err(str(result["error"]))
    return _text(json.dumps(result, indent=2, default=str))


# ---------------------------------------------------------------------------
# MCP Protocol (JSON-RPC over stdio)
# ---------------------------------------------------------------------------

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
                "jsonrpc": "2.0", "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "platform-ops", "version": "1.0.0"},
                },
            }
        elif method == "tools/list":
            response = {"jsonrpc": "2.0", "id": msg_id,
                        "result": {"tools": TOOLS}}
        elif method == "tools/call":
            result = handle_tool_call(params.get("name", ""),
                                      params.get("arguments", {}))
            response = {"jsonrpc": "2.0", "id": msg_id, "result": result}
        elif method == "notifications/initialized":
            continue
        elif method == "ping":
            response = {"jsonrpc": "2.0", "id": msg_id, "result": {}}
        else:
            response = {"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32601,
                                  "message": f"Method not found: {method}"}}

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

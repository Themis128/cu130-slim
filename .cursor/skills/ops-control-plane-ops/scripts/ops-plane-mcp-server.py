#!/usr/bin/env python3
"""ops-plane MCP Server — SocialAuto ops_watchers/ops_services control plane.

stdio MCP server (JSON-RPC, Python stdlib) exposing the two registries:

  management tools  → admin JWT (SOCIAL_ADMIN_EMAIL/PASSWORD, skill_http)
  agent tools       → X-Service-Token (OPS_WATCHER_AGENT_TOKEN)

Credentials load from env or ~/cu130-slim/.env; never returned in results.

Config (.devin/mcp_config.json):
{
  "mcpServers": {
    "ops-plane": {
      "command": "python3",
      "args": [".../ops-control-plane-ops/scripts/ops-plane-mcp-server.py"],
      "cwd": "/home/tbaltzakis/cu130-slim"
    }
  }
}
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env, request, social_api  # noqa: E402

API = env("SOCIAL_API_URL", "http://127.0.0.1:8083").rstrip("/")
AGENT_TOKEN = env("OPS_WATCHER_AGENT_TOKEN")


# ── transport helpers ────────────────────────────────────────────────────


def _admin() -> tuple[str, str]:
    return social_api()


def _mgmt(method: str, path: str, body: dict | None = None):
    api, token = _admin()
    return request(method, f"{api}{path}", token=token, data=body)


def _agent(method: str, path: str, body: dict | None = None):
    if not AGENT_TOKEN:
        raise RuntimeError("OPS_WATCHER_AGENT_TOKEN not set (env or .env)")
    return request(
        method,
        f"{API}/api/v1{path}",
        headers={"X-Service-Token": AGENT_TOKEN},
        data=body,
    )


# ── tool handlers ────────────────────────────────────────────────────────


def _ops_status(_a: dict) -> dict[str, Any]:
    api, token = _admin()
    w = request("GET", f"{api}/api/v1/ops/watchers", token=token)
    s = request("GET", f"{api}/api/v1/ops/services", token=token)
    ws, ss = w["watchers"], s["services"]

    def tally(key: str, rows: list) -> dict:
        out: dict = {}
        for r in rows:
            out[r[key]] = out.get(r[key], 0) + 1
        return out

    return {
        "watchers": {
            "total": len(ws),
            "by_executor": tally("executor", ws),
            "by_status": tally("status", ws),
            "disabled": sum(1 for x in ws if not x["enabled"]),
        },
        "services": {
            "total": len(ss),
            "by_tier": tally("tier", ss),
            "by_status": tally("status", ss),
            "pending_actions": [x["name"] for x in ss if x.get("pending_action")],
            "disabled": sum(1 for x in ss if not x["enabled"]),
        },
    }


def _watchers_list(a: dict) -> dict[str, Any]:
    q = []
    for k, param in [("tier", "tier"), ("executor", "executor"), ("kind", "kind")]:
        if a.get(k):
            q.append(f"{param}={a[k]}")
    if a.get("enabled") is not None:
        q.append(f"enabled={str(bool(a['enabled'])).lower()}")
    suffix = ("?" + "&".join(q)) if q else ""
    api, token = _admin()
    return request("GET", f"{api}/api/v1/ops/watchers{suffix}", token=token)


def _watcher_show(a: dict) -> dict[str, Any]:
    api, token = _admin()
    out = request("GET", f"{api}/api/v1/ops/watchers", token=token)
    for w in out["watchers"]:
        if w["name"] == a["name"]:
            return w
    return {"error": f"watcher not found: {a['name']}"}


def _watcher_toggle(a: dict) -> dict[str, Any]:
    return _mgmt(
        "POST",
        f"/api/v1/ops/watchers/{a['name']}/toggle",
        {"enabled": bool(a["enabled"])},
    )


def _watcher_run(a: dict) -> dict[str, Any]:
    return _mgmt("POST", f"/api/v1/ops/watchers/{a['name']}/run")


def _services_list(a: dict) -> dict[str, Any]:
    q = []
    for k in ("tier", "status"):
        if a.get(k):
            q.append(f"{k}={a[k]}")
    suffix = ("?" + "&".join(q)) if q else ""
    api, token = _admin()
    return request("GET", f"{api}/api/v1/ops/services{suffix}", token=token)


def _service_show(a: dict) -> dict[str, Any]:
    api, token = _admin()
    out = request("GET", f"{api}/api/v1/ops/services", token=token)
    for s in out["services"]:
        if s["name"] == a["name"]:
            return s
    return {"error": f"service not found: {a['name']}"}


def _service_action(a: dict) -> dict[str, Any]:
    return _mgmt(
        "POST",
        f"/api/v1/ops/services/{a['name']}/action",
        {"action": a["action"], "reason": a.get("reason", "")},
    )


def _watchers_due(_a: dict) -> dict[str, Any]:
    return _agent("GET", "/ops/watchers/due")


def _watcher_result(a: dict) -> dict[str, Any]:
    return _agent(
        "POST",
        f"/ops/watchers/{a['name']}/result",
        {
            "exit_code": int(a.get("exit_code", 0)),
            "output_tail": a.get("output_tail", ""),
            "duration_seconds": float(a.get("duration_seconds", 0.0)),
            "observed": bool(a.get("observed", False)),
        },
    )


def _watchers_sync(a: dict) -> dict[str, Any]:
    return _agent("POST", "/ops/watchers/sync", a["payload"])


def _services_pending(_a: dict) -> dict[str, Any]:
    return _agent("GET", "/ops/services/actions")


def _services_report(a: dict) -> dict[str, Any]:
    return _agent("POST", "/ops/services/report", {"reports": a["reports"]})


def _service_action_result(a: dict) -> dict[str, Any]:
    return _agent(
        "POST",
        f"/ops/services/{a['name']}/action-result",
        {"ok": bool(a["ok"]), "output_tail": a.get("output_tail", "")},
    )


def _services_sync(a: dict) -> dict[str, Any]:
    return _agent("POST", "/ops/services/sync", a["payload"])


_HANDLERS = {
    "ops_status": _ops_status,
    "ops_watchers_list": _watchers_list,
    "ops_watcher_show": _watcher_show,
    "ops_watcher_toggle": _watcher_toggle,
    "ops_watcher_run": _watcher_run,
    "ops_services_list": _services_list,
    "ops_service_show": _service_show,
    "ops_service_action": _service_action,
    "ops_watchers_due": _watchers_due,
    "ops_watcher_result": _watcher_result,
    "ops_watchers_sync": _watchers_sync,
    "ops_services_pending": _services_pending,
    "ops_services_report": _services_report,
    "ops_service_action_result": _service_action_result,
    "ops_services_sync": _services_sync,
}

_NAME = {"type": "string", "description": "registry row name"}

TOOLS = [
    {"name": "ops_status", "description": "Control-plane dashboard: watcher/service counts by executor/tier/status, disabled + pending actions.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ops_watchers_list", "description": "List ops_watchers rows. Filters: tier, executor, kind, enabled.",
     "inputSchema": {"type": "object", "properties": {
         "tier": {"type": "string"}, "executor": {"type": "string"},
         "kind": {"type": "string"}, "enabled": {"type": "boolean"}}}},
    {"name": "ops_watcher_show", "description": "Full ops_watchers row by name.",
     "inputSchema": {"type": "object", "properties": {"name": _NAME}, "required": ["name"]}},
    {"name": "ops_watcher_toggle", "description": "Enable/disable a watcher. Disabled celery watchers are skipped by watchdog_gate before they run.",
     "inputSchema": {"type": "object", "properties": {"name": _NAME, "enabled": {"type": "boolean"}}, "required": ["name", "enabled"]}},
    {"name": "ops_watcher_run", "description": "Run a watcher now: celery→send_task, host-agent→claim next_due_at. external→400.",
     "inputSchema": {"type": "object", "properties": {"name": _NAME}, "required": ["name"]}},
    {"name": "ops_services_list", "description": "List ops_services rows. Filters: tier, status.",
     "inputSchema": {"type": "object", "properties": {"tier": {"type": "string"}, "status": {"type": "string"}}}},
    {"name": "ops_service_show", "description": "Full ops_services row by name (incl. meta.allowed_actions policy).",
     "inputSchema": {"type": "object", "properties": {"name": _NAME}, "required": ["name"]}},
    {"name": "ops_service_action", "description": "Queue an allowlisted action (restart|start|stop|logs|status) on a service. Enforces meta.allowed_actions — 403 when not permitted.",
     "inputSchema": {"type": "object", "properties": {
         "name": _NAME, "action": {"type": "string", "enum": ["restart", "start", "stop", "logs", "status"]},
         "reason": {"type": "string"}}, "required": ["name", "action"]}},
    {"name": "ops_watchers_due", "description": "AGENT: due host-agent watchers + observe-only set (claims next_due_at).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ops_watcher_result", "description": "AGENT: post a watcher execution/observation result.",
     "inputSchema": {"type": "object", "properties": {
         "name": _NAME, "exit_code": {"type": "integer"}, "output_tail": {"type": "string"},
         "duration_seconds": {"type": "number"}, "observed": {"type": "boolean"}}, "required": ["name"]}},
    {"name": "ops_watchers_sync", "description": "AGENT: idempotent upsert of watcher rows {watchers:[SyncItem]}.",
     "inputSchema": {"type": "object", "properties": {"payload": {"type": "object"}}, "required": ["payload"]}},
    {"name": "ops_services_pending", "description": "AGENT: services with a queued pending_action awaiting execution.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ops_services_report", "description": "AGENT: batch service status reports [{name,status,health,detail}].",
     "inputSchema": {"type": "object", "properties": {
         "reports": {"type": "array", "items": {"type": "object"}}}, "required": ["reports"]}},
    {"name": "ops_service_action_result", "description": "AGENT: report a queued action's outcome; clears pending_action.",
     "inputSchema": {"type": "object", "properties": {
         "name": _NAME, "ok": {"type": "boolean"}, "output_tail": {"type": "string"}}, "required": ["name", "ok"]}},
    {"name": "ops_services_sync", "description": "AGENT: idempotent upsert of service rows {services:[SyncItem]} — meta merges, never clobbers allowed_actions.",
     "inputSchema": {"type": "object", "properties": {"payload": {"type": "object"}}, "required": ["payload"]}},
]


# ── MCP protocol ─────────────────────────────────────────────────────────


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error: {msg}"}], "isError": True}


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
                    "serverInfo": {"name": "ops-plane", "version": "1.0.0"},
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
            response = {"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32601, "message": f"Method not found: {method}"}}

        sys.stdout.write(json.dumps(response) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""n8n MCP Server.

A lightweight MCP (Model Context Protocol) server that wraps the self-hosted
n8n instance's REST API and CLI, exposing workflow management, execution
monitoring, deployment, and audit tools.

Runs as a stdio-based MCP server. No external dependencies beyond the Python
standard library — communicates via JSON-RPC over stdin/stdout.

Configuration (env vars):
  N8N_API_URL   - default http://localhost:5678
  N8N_API_KEY   - UI-minted API key; falls back to parsing N8N_API_KEY from
                  the repo .env file (never printed or returned in results)
  N8N_CONTAINER - docker container name, default "n8n"
  N8N_REPO_DIR  - workflow JSON dir, default <repo>/n8n-workflows

Usage in MCP config:
{
  "mcpServers": {
    "n8n": {
      "command": "python3",
      "args": ["/path/to/n8n-mcp-server.py"]
    }
  }
}

n8n 2.x notes baked into the tools:
  - `import:workflow` DEACTIVATES the workflow — always republish after import.
  - `publish:workflow --all` is deprecated; publish per-ID then restart n8n.
  - Trigger/webhook registration only takes effect after restart.

Tools exposed (13):
  Instance:
    - n8n_health: healthz + version + counts
    - n8n_list_workflows: list workflows (id, name, active)
    - n8n_get_workflow: full workflow JSON by id or name
    - n8n_list_credentials: credential names/types only (no secrets)

  Executions:
    - n8n_list_executions: recent executions with status filter
    - n8n_get_execution: execution detail incl. per-node errors
    - n8n_retry_execution: retry a failed execution

  Deploy:
    - n8n_deploy_workflow: docker cp + import + publish a repo JSON file
    - n8n_deploy_all: import + publish every JSON in n8n-workflows/, restart
    - n8n_activate_workflow / n8n_deactivate_workflow: toggle active state

  Ops:
    - n8n_audit_workflows: static audit of repo JSONs (TOTP login, hardcoded
      provider/model, live drift) — the checks that caught the 2FA outage
    - n8n_trigger_webhook: POST a JSON body to a workflow webhook
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

N8N_API = os.environ.get("N8N_API_URL", "http://localhost:5678").rstrip("/")
N8N_CONTAINER = os.environ.get("N8N_CONTAINER", "n8n")
DB_CONTAINER = os.environ.get("N8N_DB_CONTAINER", "social-postgres")
DB_USER = os.environ.get("N8N_DB_USER", "social_user")
DB_NAME = os.environ.get("N8N_DB_NAME", "n8n")
REPO_ROOT = Path(__file__).resolve().parents[4]
REPO_WORKFLOWS = Path(os.environ.get("N8N_REPO_DIR", REPO_ROOT / "n8n-workflows"))
REPO_ENV = REPO_ROOT / ".env"
TIMEOUT = int(os.environ.get("N8N_TIMEOUT", "60"))


def _api_key() -> str | None:
    """Resolve the n8n API key: env first, then the repo .env file."""
    key = os.environ.get("N8N_API_KEY", "").strip()
    if key:
        return key
    if REPO_ENV.is_file():
        for line in REPO_ENV.read_text().splitlines():
            if line.startswith("N8N_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


# ---------------------------------------------------------------------------
# HTTP helpers (n8n Public REST API v1)
# ---------------------------------------------------------------------------

def _api(method: str, path: str, body: dict | None = None) -> dict[str, Any]:
    """Call the n8n Public API. Returns parsed JSON or {"error": ...}."""
    key = _api_key()
    if not key:
        return {"error": "N8N_API_KEY not set in env or repo .env"}
    url = f"{N8N_API}/api/v1{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-N8N-API-KEY", key)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read().decode()
            return json.loads(raw) if raw else {"ok": True}
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        return {"error": f"HTTP {e.code}: {detail}"}
    except urllib.error.URLError as e:
        return {"error": f"n8n API not reachable: {e}"}
    except Exception as e:
        return {"error": str(e)}


def _cli(*args: str, timeout: int = 120) -> dict[str, Any]:
    """Run an n8n CLI command inside the container."""
    try:
        result = subprocess.run(
            ["docker", "exec", N8N_CONTAINER, "n8n", *args],
            capture_output=True, text=True, timeout=timeout,
        )
        out = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            return {"error": out[-500:] or f"exit {result.returncode}"}
        return {"output": out}
    except subprocess.TimeoutExpired:
        return {"error": f"CLI timed out ({timeout}s)"}
    except FileNotFoundError:
        return {"error": "docker CLI not found"}


def _restart() -> dict[str, Any]:
    r = subprocess.run(
        ["docker", "restart", N8N_CONTAINER],
        capture_output=True, text=True, timeout=60,
    )
    return {"restarted": r.returncode == 0}


def _text(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(msg: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"Error: {msg}"}], "isError": True}


def _result(data: dict[str, Any]) -> dict[str, Any]:
    if "error" in data:
        return _err(data["error"])
    return _text(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "n8n_health",
        "description": "Check n8n health endpoint, container status, and workflow counts (active/inactive).",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "n8n_list_workflows",
        "description": "List workflows with id, name, active state. Optional name filter.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Filter by name substring (case-insensitive)"},
                "active": {"type": "boolean", "description": "Filter by active state"},
            },
        },
    },
    {
        "name": "n8n_get_workflow",
        "description": "Get full workflow JSON (nodes + connections) by id or name.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow": {"type": "string", "description": "Workflow id or name"},
            },
            "required": ["workflow"],
        },
    },
    {
        "name": "n8n_list_credentials",
        "description": "List credential names, types, and ids. Never returns secret values.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "n8n_list_executions",
        "description": "List recent executions with workflow/status filters. Excludes node payload data.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow": {"type": "string", "description": "Workflow id or name"},
                "status": {"type": "string", "enum": ["success", "error", "running", "waiting", "canceled", "crashed"]},
                "limit": {"type": "integer", "default": 10, "maximum": 50},
            },
        },
    },
    {
        "name": "n8n_get_execution",
        "description": "Get execution detail: status, timing, last node, and per-node error messages.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "integer"},
            },
            "required": ["execution_id"],
        },
    },
    {
        "name": "n8n_retry_execution",
        "description": "Retry a failed execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "execution_id": {"type": "integer"},
            },
            "required": ["execution_id"],
        },
    },
    {
        "name": "n8n_deploy_workflow",
        "description": "Deploy one workflow JSON from the repo: copy into container, import, publish, report active state. Restart n8n afterwards for triggers to register.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file": {"type": "string", "description": "Filename inside n8n-workflows/ (e.g. twitter-text-post.json)"},
                "restart": {"type": "boolean", "default": False, "description": "Restart n8n after deploy (required for webhook/schedule registration)"},
            },
            "required": ["file"],
        },
    },
    {
        "name": "n8n_deploy_all",
        "description": "Import + publish every JSON in n8n-workflows/ and restart n8n. Use after bulk workflow edits.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "restart": {"type": "boolean", "default": True},
            },
        },
    },
    {
        "name": "n8n_activate_workflow",
        "description": "Activate a workflow (enables triggers after next restart).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow": {"type": "string", "description": "Workflow id or name"},
            },
            "required": ["workflow"],
        },
    },
    {
        "name": "n8n_deactivate_workflow",
        "description": "Deactivate a workflow (stops triggers).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "workflow": {"type": "string", "description": "Workflow id or name"},
            },
            "required": ["workflow"],
        },
    },
    {
        "name": "n8n_audit_workflows",
        "description": "Static audit of repo workflow JSONs + live drift: missing TOTP on SocialAuto login nodes, hardcoded AI provider/model pairs that bypass auto-routing, inactive live workflows, webhook paths.",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "n8n_trigger_webhook",
        "description": "POST a JSON body to a workflow's production webhook (e.g. /webhook/twitter-text-post). Supports dry-run flags like publish:false.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Webhook path, e.g. twitter-text-post or /webhook/twitter-text-post"},
                "body": {"type": "object", "description": "JSON body to POST"},
                "timeout": {"type": "integer", "default": 120},
            },
            "required": ["path"],
        },
    },
]


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def _workflow_id(ref: str) -> str | dict[str, Any]:
    """Resolve a workflow id-or-name to an id."""
    res = _api("GET", "/workflows?limit=100")
    if "error" in res:
        return res
    for w in res.get("data", []):
        if w.get("id") == ref or (w.get("name") or "").lower() == ref.lower():
            return w["id"]
    for w in res.get("data", []):  # substring fallback
        if ref.lower() in (w.get("name") or "").lower():
            return w["id"]
    return {"error": f"workflow not found: {ref}"}


def _psql(sql: str) -> str:
    """Run a read-only SQL query against the n8n Postgres DB."""
    r = subprocess.run(
        ["docker", "exec", DB_CONTAINER, "psql", "-U", DB_USER, "-d", DB_NAME, "-tAc", sql],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip()[:300])
    return r.stdout.strip()


def _rehydrate(data: list) -> Any:
    """Rehydrate n8n 2.x's deduplicated execution_data format.

    The row stores a flat array where dict/list string values are indices into
    the same array; non-string values are literals. Terminal strings live as
    array elements.
    """
    def resolve(v: Any) -> Any:
        if isinstance(v, str) and v.isdigit() and int(v) < len(data):
            return walk(int(v))
        return v

    def walk(idx: int) -> Any:
        node = data[idx]
        if isinstance(node, dict):
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return walk(0) if isinstance(data, list) and data else data


def _db_executions(args: dict[str, Any]) -> dict[str, Any]:
    """List executions via Postgres (fallback when the API key lacks scope)."""
    limit = min(int(args.get("limit", 10)), 50)
    where = ""
    if args.get("status"):
        s = args["status"].replace("'", "")
        where += f" AND e.status='{s}'"
    if args.get("workflow"):
        ref = args["workflow"].replace("'", "")
        where += (
            f" AND (e.\"workflowId\"='{ref}' OR w.name ILIKE '%{ref}%')"
        )
    rows = _psql(
        "SELECT e.id, e.\"workflowId\", w.name, e.status, e.finished, "
        "e.\"startedAt\"::timestamp(0), e.\"stoppedAt\"::timestamp(0) "
        "FROM execution_entity e LEFT JOIN workflow_entity w "
        "ON w.id=e.\"workflowId\" WHERE true " + where +
        f" ORDER BY e.id DESC LIMIT {limit}"
    )
    out = []
    for line in rows.splitlines():
        if not line:
            continue
        eid, wid, wname, status, fin, start, stop = line.split("|")
        out.append({"id": int(eid), "workflowId": wid, "workflow": wname,
                    "status": status, "finished": fin == "t",
                    "startedAt": start, "stoppedAt": stop})
    return {"data": out}


def _db_execution_detail(execution_id: int) -> dict[str, Any]:
    """Execution detail via Postgres, rehydrating n8n 2.x execution_data."""
    meta = _psql(
        "SELECT e.id, e.\"workflowId\", w.name, e.status, e.finished, "
        "e.\"startedAt\"::timestamp(0), e.\"stoppedAt\"::timestamp(0) "
        "FROM execution_entity e LEFT JOIN workflow_entity w "
        "ON w.id=e.\"workflowId\" WHERE e.id=" + str(int(execution_id))
    )
    if not meta:
        return {"error": f"execution {execution_id} not found"}
    eid, wid, wname, status, fin, start, stop = meta.split("|")
    raw = _psql(
        'SELECT data FROM execution_data WHERE "executionId"=' + str(int(execution_id))
    )
    data = {}
    try:
        data = _rehydrate(json.loads(raw))
    except Exception:
        pass
    return {
        "id": int(eid), "workflowId": wid, "workflow": wname,
        "status": status, "finished": fin == "t",
        "startedAt": start, "stoppedAt": stop,
        "lastNodeExecuted": (data.get("resultData") or {}).get("lastNodeExecuted"),
        "node_errors": _extract_node_errors(data),
    }


def _extract_node_errors(data: Any) -> list[str]:
    """Pull per-node error messages out of an execution's resultData."""
    errors = []
    if isinstance(data, dict):
        run_data = data.get("resultData", {}).get("runData", {})
        if isinstance(run_data, dict):
            for node, runs in run_data.items():
                for run in runs or []:
                    err = (run or {}).get("error")
                    if err:
                        errors.append(f"{node}: {str(err.get('message', err))[:200]}")
    return errors


def handle_tool_call(name: str, args: dict[str, Any]) -> dict[str, Any]:
    if name == "n8n_health":
        try:
            req = urllib.request.Request(f"{N8N_API}/healthz")
            healthy = urllib.request.urlopen(req, timeout=10).status == 200
        except Exception as e:
            return _err(f"healthz failed: {e}")
        ps = subprocess.run(
            ["docker", "ps", "--filter", f"name={N8N_CONTAINER}",
             "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=15,
        )
        wfs = _api("GET", "/workflows?limit=100")
        counts = {}
        for w in wfs.get("data", []):
            counts["active" if w.get("active") else "inactive"] = \
                counts.get("active" if w.get("active") else "inactive", 0) + 1
        return _text(
            f"healthz: {'ok' if healthy else 'FAIL'}\n"
            f"container: {ps.stdout.strip() or 'not running'}\n"
            f"workflows: {counts}"
        )

    if name == "n8n_list_workflows":
        res = _api("GET", "/workflows?limit=100")
        if "error" in res:
            return _err(res["error"])
        rows = []
        for w in res.get("data", []):
            if args.get("name") and args["name"].lower() not in (w.get("name") or "").lower():
                continue
            if "active" in args and w.get("active") != args["active"]:
                continue
            rows.append({
                "id": w.get("id"), "name": w.get("name"),
                "active": w.get("active"), "updatedAt": w.get("updatedAt"),
            })
        return _text(json.dumps(rows, indent=2))

    if name == "n8n_get_workflow":
        wid = _workflow_id(args["workflow"])
        if isinstance(wid, dict):
            return _err(wid["error"])
        return _result(_api("GET", f"/workflows/{wid}"))

    if name == "n8n_list_credentials":
        res = _api("GET", "/credentials?limit=100")
        if "error" in res:
            return _err(res["error"])
        rows = [{"id": c.get("id"), "name": c.get("name"), "type": c.get("type")}
                for c in res.get("data", [])]
        return _text(json.dumps(rows, indent=2))

    if name == "n8n_list_executions":
        qs = f"?limit={min(int(args.get('limit', 10)), 50)}"
        if args.get("status"):
            qs += f"&status={args['status']}"
        if args.get("workflow"):
            wid = _workflow_id(args["workflow"])
            if isinstance(wid, dict):
                return _err(wid["error"])
            qs += f"&workflowId={wid}"
        res = _api("GET", f"/executions{qs}")
        if "error" in res and "403" in res["error"]:
            try:
                res = _db_executions(args)
            except Exception as e:
                return _err(f"API 403 + DB fallback failed: {e}")
        if "error" in res:
            return _err(res["error"])
        rows = [{
            "id": e.get("id"), "workflowId": e.get("workflowId"),
            "status": e.get("status"), "finished": e.get("finished"),
            "startedAt": e.get("startedAt"), "stoppedAt": e.get("stoppedAt"),
        } for e in res.get("data", [])]
        return _text(json.dumps(rows, indent=2))

    if name == "n8n_get_execution":
        res = _api("GET", f"/executions/{args['execution_id']}?includeData=true")
        if "error" in res and "403" in res["error"]:
            try:
                res = _db_execution_detail(args["execution_id"])
            except Exception as e:
                return _err(f"API 403 + DB fallback failed: {e}")
        if "error" in res:
            return _err(res["error"])
        if "node_errors" in res:  # already shaped by the DB fallback
            return _text(json.dumps(res, indent=2, default=str))
        out = {
            "id": res.get("id"), "workflowId": res.get("workflowId"),
            "status": res.get("status"), "finished": res.get("finished"),
            "startedAt": res.get("startedAt"), "stoppedAt": res.get("stoppedAt"),
            "lastNodeExecuted": (res.get("data") or {}).get("resultData", {}).get("lastNodeExecuted"),
            "node_errors": _extract_node_errors(res.get("data")),
        }
        return _text(json.dumps(out, indent=2, default=str))

    if name == "n8n_retry_execution":
        return _result(_api("POST", f"/executions/{args['execution_id']}/retry"))

    if name == "n8n_activate_workflow" or name == "n8n_deactivate_workflow":
        wid = _workflow_id(args["workflow"])
        if isinstance(wid, dict):
            return _err(wid["error"])
        verb = "activate" if name == "n8n_activate_workflow" else "deactivate"
        return _result(_api("POST", f"/workflows/{wid}/{verb}"))

    if name == "n8n_deploy_workflow":
        src = REPO_WORKFLOWS / args["file"]
        if not src.is_file():
            return _err(f"file not found: {src}")
        cp = subprocess.run(
            ["docker", "cp", str(src), f"{N8N_CONTAINER}:/tmp/wf-deploy.json"],
            capture_output=True, text=True, timeout=30,
        )
        if cp.returncode != 0:
            return _err(f"docker cp failed: {cp.stderr.strip()}")
        imp = _cli("import:workflow", "--input=/tmp/wf-deploy.json")
        if "error" in imp:
            return _err(imp["error"])
        wid = _workflow_id(args["file"].replace(".json", ""))
        published = "n/a"
        if isinstance(wid, str):
            pub = _cli("publish:workflow", f"--id={wid}")
            published = "ok" if "error" not in pub else pub["error"][:200]
        out = {"imported": args["file"], "published": published}
        if args.get("restart"):
            out.update(_restart())
        return _text(json.dumps(out, indent=2))

    if name == "n8n_deploy_all":
        files = sorted(REPO_WORKFLOWS.glob("*.json"))
        results = {}
        for f in files:
            cp = subprocess.run(
                ["docker", "cp", str(f), f"{N8N_CONTAINER}:/tmp/wf-deploy.json"],
                capture_output=True, text=True, timeout=30,
            )
            if cp.returncode != 0:
                results[f.name] = f"cp failed: {cp.stderr.strip()[:100]}"
                continue
            imp = _cli("import:workflow", "--input=/tmp/wf-deploy.json")
            results[f.name] = "imported" if "error" not in imp else imp["error"][:100]
        wfs = _api("GET", "/workflows?limit=100")
        pub_ok = pub_fail = 0
        for w in wfs.get("data", []):
            pub = _cli("publish:workflow", f"--id={w['id']}")
            pub_ok, pub_fail = (pub_ok + 1, pub_fail) if "error" not in pub else (pub_ok, pub_fail + 1)
        out = {"imported": results, "published": f"{pub_ok} ok, {pub_fail} failed"}
        if args.get("restart", True):
            out.update(_restart())
        return _text(json.dumps(out, indent=2))

    if name == "n8n_audit_workflows":
        report = []
        live = _api("GET", "/workflows?limit=100")
        live_by_name = {w.get("name"): w for w in live.get("data", [])}
        for f in sorted(REPO_WORKFLOWS.glob("*.json")):
            try:
                wf = json.loads(f.read_text())
            except json.JSONDecodeError as e:
                report.append(f"{f.name}: INVALID JSON ({e})")
                continue
            issues, notes = [], []
            nodes = wf.get("nodes", [])
            for n in nodes:
                body = str(n.get("parameters", {}))
                nname = n.get("name", "?")
                if "auth/login" in body:
                    if '"otp"' not in body and "Generate TOTP" not in json.dumps(wf.get("connections", {})) \
                            and not any("totp" in (m.get("name") or "").lower() for m in nodes):
                        issues.append(f"login node '{nname}' has no otp/TOTP step")
                if "generate-content" in body:
                    if '"provider"' in body or '"model"' in body:
                        m = re.search(r'\\"model\\":\s*\\"([^\\]+)\\"', body)
                        issues.append(f"'{nname}' pins provider/model"
                                      + (f" ({m.group(1)})" if m else "")
                                      + " — bypasses auto-routing")
                    if "smollm2" in body:
                        issues.append(f"'{nname}' uses broken ai/smollm2 (empty content)")
                if n.get("type") == "n8n-nodes-base.webhook":
                    path = n.get("parameters", {}).get("path")
                    if path:
                        notes.append(f"webhook: /webhook/{path}")
            lw = live_by_name.get(wf.get("name"))
            if lw is None:
                notes.append("not deployed live")
            elif not lw.get("active"):
                issues.append("live workflow INACTIVE")
            line = f"{f.name}"
            if issues:
                line += "\n  ISSUES: " + "; ".join(issues)
            if notes:
                line += "\n  " + "; ".join(notes)
            if not issues and not notes:
                line += ": clean"
            report.append(line)
        inactive = [n for n, w in live_by_name.items() if not w.get("active")]
        report.append(f"\nlive: {len(live_by_name)} workflows, {len(inactive)} inactive"
                      + (f" ({', '.join(inactive)})" if inactive else ""))
        return _text("\n".join(report))

    if name == "n8n_trigger_webhook":
        path = args["path"]
        if not path.startswith("/webhook"):
            path = f"/webhook/{path.lstrip('/')}"
        body = json.dumps(args.get("body", {})).encode()
        req = urllib.request.Request(
            f"{N8N_API}{path}", data=body, method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=int(args.get("timeout", 120))) as resp:
                return _text(f"HTTP {resp.status}\n{resp.read().decode()[:2000]}")
        except urllib.error.HTTPError as e:
            return _err(f"HTTP {e.code}: {e.read().decode()[:500]}")
        except Exception as e:
            return _err(str(e))

    return _err(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# MCP Protocol (JSON-RPC over stdio)
# ---------------------------------------------------------------------------

def main() -> None:
    """Main MCP server loop (JSON-RPC over stdio)."""
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
                    "serverInfo": {"name": "n8n-mcp-server", "version": "1.0.0"},
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

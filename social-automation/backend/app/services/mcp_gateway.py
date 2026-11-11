"""MCP gateway client — lets SocialAuto workers/API call external MCP servers.

Registry sources (merged, env wins on name conflicts):
- ``MCP_GATEWAY_SERVERS`` env var: JSON list of server defs
- in-cluster defaults: the MCP servers already deployed in this compose stack

Server def shape::

    {"name": "nextcloud", "url": "http://nextcloud-mcp-server:8000/mcp",
     "description": "...", "enabled": true,
     "headers": {"Authorization": "Bearer ..."}}  # optional static headers

Only streamable-http transport is supported (every in-cluster MCP server uses
it). Stdio servers need a sidecar adapter — they are not reachable from inside
the api/worker containers anyway.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PROTOCOL_VERSION = "2025-03-26"
_TOOLS_TTL_SECONDS = 300.0

# Servers deployed inside this compose stack — reachable on docker networks.
_DEFAULT_SERVERS: list[dict[str, Any]] = [
    {
        "name": "linkedin",
        "url": os.getenv("LINKEDIN_MCP_SERVER_URL", "http://stack-ops:9227/mcp"),
        "host_header": "localhost:9227",
        "description": "LinkedIn MCP — profile/org reads and writes via stored session",
    },
    {
        "name": "airbyte",
        "url": os.getenv("AIRBYTE_MCP_SERVER_URL", "http://stack-ops:9228/mcp"),
        "host_header": "localhost:9228",
        "description": "Airbyte MCP — data-connector introspection",
    },
    {
        "name": "nextcloud",
        "url": os.getenv("NEXTCLOUD_MCP_URL", "http://nextcloud-mcp-server:8000/mcp"),
        "description": "Nextcloud MCP — 110+ tools over the cloudless.gr workspace",
    },
]

_tools_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_session_cache: dict[str, str] = {}


def load_registry() -> dict[str, dict[str, Any]]:
    """Merged server registry: env JSON overrides/extends the defaults."""
    registry: dict[str, dict[str, Any]] = {s["name"]: dict(s) for s in _DEFAULT_SERVERS}
    raw = os.getenv("MCP_GATEWAY_SERVERS", "").strip()
    if raw:
        try:
            for s in json.loads(raw):
                if s.get("name") and s.get("url"):
                    registry[s["name"]] = s
        except json.JSONDecodeError:
            logger.warning("MCP_GATEWAY_SERVERS is not valid JSON — ignoring")
    for s in registry.values():
        s.setdefault("enabled", True)
    return registry


def _headers(server: dict[str, Any]) -> dict[str, str]:
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    extra = server.get("headers") or {}
    h.update({str(k): str(v) for k, v in extra.items()})
    # FastMCP streamable-http validates Host against the bound address —
    # proxies like stack-ops need the origin-style Host header (421 fix,
    # same as app.api.mcp).
    host_header = server.get("host_header")
    if host_header:
        h["Host"] = host_header
    return h


async def _rpc(
    client: httpx.AsyncClient,
    server: dict[str, Any],
    method: str,
    params: dict[str, Any] | None,
    req_id: int,
) -> dict[str, Any]:
    """One JSON-RPC call over streamable-http with session bootstrap."""
    url = server["url"]
    name = server["name"]

    # Ensure an initialized session exists (initialize once per server).
    session_id = _session_cache.get(name, "")
    headers = _headers(server)
    if session_id:
        headers["Mcp-Session-Id"] = session_id

    if not session_id:
        resp = await client.post(
            url,
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "socialauto-gateway", "version": "1.0"},
                },
            },
        )
        resp.raise_for_status()
        sid = resp.headers.get("mcp-session-id", "")
        if sid:
            _session_cache[name] = sid
            headers["Mcp-Session-Id"] = sid
        await client.post(
            url,
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )

    resp = await client.post(
        url,
        headers=headers,
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}},
    )
    # Session may have expired server-side — re-init once and retry.
    if resp.status_code in (400, 404) and "Mcp-Session-Id" in headers:
        _session_cache.pop(name, None)
        _tools_cache.pop(name, None)
        return await _rpc(client, server, method, params, req_id)
    resp.raise_for_status()
    return _parse_jsonrpc(resp)


def _parse_jsonrpc(resp: httpx.Response) -> dict[str, Any]:
    """Extract the JSON-RPC payload from a JSON or SSE response body."""
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype or resp.text.lstrip().startswith("event:"):
        for line in resp.text.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
        return {}
    try:
        return resp.json()
    except json.JSONDecodeError:
        return {}


async def list_tools(server_name: str, *, timeout: float = 20.0) -> dict[str, Any]:
    """Return {online, tools[], error?} for one registry server."""
    registry = load_registry()
    server = registry.get(server_name)
    if not server:
        return {"online": False, "error": f"unknown server '{server_name}'"}
    if not server.get("enabled", True):
        return {"online": False, "error": "server disabled"}

    cached = _tools_cache.get(server_name)
    if cached and time.monotonic() - cached[0] < _TOOLS_TTL_SECONDS:
        return {"online": True, "tools": cached[1], "cached": True}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            result = await _rpc(client, server, "tools/list", {}, 1)
    except Exception as exc:
        return {"online": False, "error": str(exc)[:300]}

    if "error" in result:
        return {"online": False, "error": str(result["error"])[:300]}
    tools = result.get("result", {}).get("tools", [])
    slim = [
        {
            "name": t.get("name"),
            "description": (t.get("description") or "")[:300],
            "inputSchema": t.get("inputSchema"),
        }
        for t in tools
    ]
    _tools_cache[server_name] = (time.monotonic(), slim)
    return {"online": True, "tools": slim}


async def call_tool(
    server_name: str,
    tool: str,
    arguments: dict[str, Any] | None = None,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Invoke a tool on a registry server. Returns the raw JSON-RPC result."""
    registry = load_registry()
    server = registry.get(server_name)
    if not server:
        return {"ok": False, "error": f"unknown server '{server_name}'"}
    if not server.get("enabled", True):
        return {"ok": False, "error": "server disabled"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            result = await _rpc(
                client,
                server,
                "tools/call",
                {"name": tool, "arguments": arguments or {}},
                1,
            )
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}

    if "error" in result:
        return {"ok": False, "error": result["error"]}
    return {"ok": True, "result": result.get("result", {})}


async def list_all() -> dict[str, Any]:
    """Registry + per-server tool listing for the status endpoint."""
    registry = load_registry()
    out: dict[str, Any] = {}
    for name, server in registry.items():
        if not server.get("enabled", True):
            out[name] = {"enabled": False, "url": server["url"]}
            continue
        info = await list_tools(name)
        out[name] = {
            "enabled": True,
            "url": server["url"],
            "description": server.get("description", ""),
            **info,
        }
    return out

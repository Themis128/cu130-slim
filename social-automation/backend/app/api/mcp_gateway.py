"""MCP gateway REST API — discover and invoke tools on external MCP servers.

Endpoints (all under /api/v1/mcp-gateway):
- GET  /servers            registry + live tool listing per server
- GET  /servers/{name}/tools
- POST /servers/{name}/call  {"tool": "...", "arguments": {...}}

Registry: MCP_GATEWAY_SERVERS env JSON + in-cluster defaults
(app.services.mcp_gateway). Auth: standard bearer session.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.api.auth import get_current_user
from app.models.user import User
from app.services import mcp_gateway

router = APIRouter()


class CallBody(BaseModel):
    tool: str
    arguments: dict[str, Any] | None = None
    timeout: float | None = None


@router.get("/servers")
async def list_servers(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    _ = current_user
    return {"servers": await mcp_gateway.list_all()}


@router.get("/servers/{name}/tools")
async def server_tools(name: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    _ = current_user
    result = await mcp_gateway.list_tools(name)
    if not result.get("online") and "unknown server" in str(result.get("error", "")):
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/servers/{name}/call")
async def call_server_tool(
    name: str,
    body: CallBody,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _ = current_user
    registry = mcp_gateway.load_registry()
    if name not in registry:
        raise HTTPException(status_code=404, detail=f"unknown server '{name}'")
    result = await mcp_gateway.call_tool(
        name,
        body.tool,
        body.arguments,
        timeout=body.timeout or 60.0,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=502, detail=result.get("error", "tool call failed"))
    return result

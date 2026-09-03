"""MCP stack endpoints: health, tools, and session status for all browser sidecars and MCP servers."""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.auth import get_current_user
from app.models.user import User

router = APIRouter()

# Service URLs from environment
LINKEDIN_SIDECAR_URL = os.getenv("LINKEDIN_BROWSER_SIDECAR_URL", "http://linkedin-browser-sidecar:9225")
LINKEDIN_MCP_URL = os.getenv("LINKEDIN_MCP_SERVER_URL", "http://linkedin-mcp-server:9227/mcp")
AIRBYTE_MCP_URL = os.getenv("AIRBYTE_MCP_SERVER_URL", "http://airbyte-mcp-server:9228/mcp")
FACEBOOK_SIDECAR_URL = os.getenv("FACEBOOK_BROWSER_SIDECAR_URL", "http://facebook-browser-sidecar:9226")
INSTAGRAM_SIDECAR_URL = os.getenv("INSTAGRAM_SIDECAR_URL", "http://instagram-private-api:8000")


async def _check_http(url: str, timeout: float = 5.0) -> dict[str, Any]:
    """Quick HTTP health check."""
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            ok = resp.status_code < 500
            data: dict[str, Any] = {}
            try:
                data = resp.json()
            except Exception:
                data = {"text": resp.text[:200]}
            return {"online": ok, "status_code": resp.status_code, "data": data}
    except Exception as e:
        return {"online": False, "error": str(e)}


def _parse_sse_response(text: str) -> dict[str, Any]:
    """Parse a Server-Sent Events response from an MCP server."""
    import logging

    log = logging.getLogger(__name__)
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                return json.loads(line[6:])
            except Exception:
                log.debug("Failed to parse SSE data line: %s", line[:80])
    return {}


async def _mcp_initialize_and_list_tools(url: str, timeout: float = 15.0) -> dict[str, Any]:
    """
    Initialize an MCP session over streamable-http and list tools.

    Uses a single httpx.AsyncClient with connection pooling so the
    session cookie/ID stays consistent across requests.

    Note: FastMCP's streamable-http transport validates the Host header
    against the server's bound address. When calling from another container,
    we must override the Host header to 'localhost' to avoid 421 errors.
    """
    try:
        # Extract the port from the URL to build the correct Host header
        from urllib.parse import urlparse

        parsed = urlparse(url)
        host_header = f"localhost:{parsed.port}" if parsed.port else "localhost"

        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": host_header,
            },
        ) as client:
            # Step 1: Initialize
            resp = await client.post(
                url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "socialauto", "version": "1.0"},
                    },
                },
            )
            if resp.status_code != 200:
                return {"online": False, "error": f"Initialize HTTP {resp.status_code}: {resp.text[:100]}"}

            session_id = resp.headers.get("mcp-session-id", "")
            if not session_id:
                # Some MCP servers don't require a session ID
                session_id = ""

            # Step 2: Send initialized notification
            headers: dict[str, str] = {}
            if session_id:
                headers["Mcp-Session-Id"] = session_id
            await client.post(
                url,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )

            # Step 3: List tools
            resp2 = await client.post(
                url,
                headers=headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            )
            if resp2.status_code != 200:
                return {
                    "online": True,
                    "session_id": session_id[:12] + "..." if session_id else "none",
                    "tools": [],
                    "warning": f"tools/list returned HTTP {resp2.status_code}",
                }

            parsed = _parse_sse_response(resp2.text)
            raw_tools = parsed.get("result", {}).get("tools", [])
            tools = [
                {"name": t["name"], "description": (t.get("description") or "")[:200]}
                for t in raw_tools
            ]

            return {
                "online": True,
                "session_id": session_id[:12] + "..." if session_id else "none",
                "tools": tools,
            }
    except Exception as e:
        return {"online": False, "error": str(e)}


@router.get("/stack")
async def get_mcp_stack_status(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Get health and tool list for all MCP servers and browser sidecars."""
    _ = current_user

    tasks = {
        "linkedin_sidecar": _check_http(f"{LINKEDIN_SIDECAR_URL}/health"),
        "facebook_sidecar": _check_http(f"{FACEBOOK_SIDECAR_URL}/health"),
        "instagram_sidecar": _check_http(f"{INSTAGRAM_SIDECAR_URL}/health"),
        "linkedin_mcp": _mcp_initialize_and_list_tools(LINKEDIN_MCP_URL),
        "airbyte_mcp": _mcp_initialize_and_list_tools(AIRBYTE_MCP_URL),
    }
    results: dict[str, Any] = {}
    gathered = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for (key, _), result in zip(tasks.items(), gathered):
        if isinstance(result, Exception):
            results[key] = {"online": False, "error": str(result)}
        else:
            results[key] = result

    services = [
        {
            "id": "linkedin_sidecar",
            "name": "LinkedIn Browser Sidecar",
            "type": "browser_sidecar",
            "url": LINKEDIN_SIDECAR_URL,
            "description": "Playwright browser automation for LinkedIn profile editing and posting",
            "capabilities": [
                "Edit headline, about, location, website",
                "Upload profile/cover photo",
                "Add experience, education, skills",
                "Post to personal and company feeds",
                "Company page management",
            ],
            **results.get("linkedin_sidecar", {}),
        },
        {
            "id": "facebook_sidecar",
            "name": "Facebook Browser Sidecar",
            "type": "browser_sidecar",
            "url": FACEBOOK_SIDECAR_URL,
            "description": "Playwright browser automation for Facebook profile and page operations",
            "capabilities": [
                "Personal profile posting (text/photo/link/video)",
                "Page posting and management",
                "Profile field edits (bio, work, education)",
                "Profile/cover photo upload",
            ],
            **results.get("facebook_sidecar", {}),
        },
        {
            "id": "instagram_sidecar",
            "name": "Instagram Private API Sidecar",
            "type": "browser_sidecar",
            "url": INSTAGRAM_SIDECAR_URL,
            "description": "aiograpi-rest sidecar for Instagram private API operations",
            "capabilities": [
                "Login and session management",
                "Photo/video posting",
                "Story publishing",
                "Profile field writes",
            ],
            **results.get("instagram_sidecar", {}),
        },
        {
            "id": "linkedin_mcp",
            "name": "LinkedIn MCP Server",
            "type": "mcp_server",
            "url": LINKEDIN_MCP_URL,
            "description": "stickerdaniel/linkedin-mcp-server — read profiles, search people/jobs, messaging, feed",
            "capabilities": [
                "Read any LinkedIn profile (experience, education, skills)",
                "Search people, companies, jobs, posts",
                "Read inbox and conversations",
                "Send messages and connection requests",
                "Get home feed and company posts",
            ],
            **results.get("linkedin_mcp", {}),
        },
        {
            "id": "airbyte_mcp",
            "name": "Airbyte MCP Server",
            "type": "mcp_server",
            "url": AIRBYTE_MCP_URL,
            "description": "airbyte-mcp — data connector orchestration (Facebook Marketing, Stripe, Postgres, 500+ sources)",
            "capabilities": [
                "List and run Airbyte connectors",
                "Facebook Marketing ad campaigns, insights, creatives",
                "Data sync to local cache or Airbyte Cloud",
                "500+ source connectors available",
            ],
            **results.get("airbyte_mcp", {}),
        },
    ]

    online_count = sum(1 for s in services if s.get("online"))
    return {
        "status": "ok",
        "total_services": len(services),
        "online_services": online_count,
        "services": services,
    }


@router.get("/stack/{service_id}/screenshot")
async def get_screenshot(service_id: str, current_user: User = Depends(get_current_user)):
    """Get a screenshot from a browser sidecar."""
    _ = current_user
    url_map = {
        "linkedin_sidecar": f"{LINKEDIN_SIDECAR_URL}/screenshot",
        "facebook_sidecar": f"{FACEBOOK_SIDECAR_URL}/screenshot",
    }
    url = url_map.get(service_id)
    if not url:
        raise HTTPException(status_code=404, detail=f"No screenshot endpoint for {service_id}")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return Response(content=resp.content, media_type="image/png")
            raise HTTPException(status_code=resp.status_code, detail="Sidecar returned error")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/stack/{service_id}/session")
async def check_session(service_id: str, current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Check or refresh the session for a browser sidecar."""
    _ = current_user
    url_map = {
        "linkedin_sidecar": f"{LINKEDIN_SIDECAR_URL}/session",
        "facebook_sidecar": f"{FACEBOOK_SIDECAR_URL}/session",
    }
    url = url_map.get(service_id)
    if not url:
        raise HTTPException(status_code=404, detail=f"No session endpoint for {service_id}")

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(url)
            return {"status": "ok", "result": resp.json()}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/linkedin/profile")
async def linkedin_mcp_get_profile(current_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Read the authenticated user's LinkedIn profile via the MCP server."""
    _ = current_user
    try:
        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": "localhost:9227",
            },
        ) as client:
            # Initialize
            resp = await client.post(
                LINKEDIN_MCP_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "socialauto", "version": "1.0"},
                    },
                },
            )
            session_id = resp.headers.get("mcp-session-id", "")
            headers: dict[str, str] = {}
            if session_id:
                headers["Mcp-Session-Id"] = session_id

            # Notify initialized
            await client.post(
                LINKEDIN_MCP_URL,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )

            # Call get_my_profile
            resp2 = await client.post(
                LINKEDIN_MCP_URL,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "get_my_profile", "arguments": {}},
                },
            )
            parsed = _parse_sse_response(resp2.text)
            content = parsed.get("result", {}).get("content", [])
            text = ""
            for c in content:
                if c.get("type") == "text":
                    text = c["text"]
                    break
            return {"status": "ok", "profile": text}
    except Exception as e:
        return {"status": "error", "error": str(e)}


@router.post("/linkedin/search-people")
async def linkedin_mcp_search_people(
    keywords: str = "",
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """Search people on LinkedIn via the MCP server."""
    _ = current_user
    try:
        async with httpx.AsyncClient(
            timeout=60.0,
            follow_redirects=True,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "Host": "localhost:9227",
            },
        ) as client:
            resp = await client.post(
                LINKEDIN_MCP_URL,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "socialauto", "version": "1.0"},
                    },
                },
            )
            session_id = resp.headers.get("mcp-session-id", "")
            headers: dict[str, str] = {}
            if session_id:
                headers["Mcp-Session-Id"] = session_id

            await client.post(
                LINKEDIN_MCP_URL,
                headers=headers,
                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            )

            resp2 = await client.post(
                LINKEDIN_MCP_URL,
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {"name": "search_people", "arguments": {"keywords": keywords}},
                },
            )
            parsed = _parse_sse_response(resp2.text)
            content = parsed.get("result", {}).get("content", [])
            text = ""
            for c in content:
                if c.get("type") == "text":
                    text = c["text"]
                    break
            return {"status": "ok", "results": text}
    except Exception as e:
        return {"status": "error", "error": str(e)}

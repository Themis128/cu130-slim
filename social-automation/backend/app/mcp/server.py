"""SocialAuto MCP Server — exposes SocialAuto's API to AI agents.

Run with: python -m app.mcp.server

Environment variables:
- SOCIALAUTO_URL: Base URL of the SocialAuto API (default: http://localhost:8083)
- SOCIALAUTO_TOKEN: Bearer token for authentication
- SOCIALAUTO_ADMIN_EMAIL: Admin email for auto-login (alternative to SOCIALAUTO_TOKEN)
- SOCIALAUTO_ADMIN_PASSWORD: Admin password for auto-login
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
    Tool,
)

server = Server("socialauto")

API_URL = os.environ.get("SOCIALAUTO_URL", "http://localhost:8083")
API_TOKEN = os.environ.get("SOCIALAUTO_TOKEN", "")


async def _get_token() -> str:
    """Get API token from env or auto-login."""
    global API_TOKEN
    if API_TOKEN:
        return API_TOKEN
    email = os.environ.get("SOCIALAUTO_ADMIN_EMAIL", "")
    password = os.environ.get("SOCIALAUTO_ADMIN_PASSWORD", "")
    if not email or not password:
        raise RuntimeError(
            "Set SOCIALAUTO_TOKEN or SOCIALAUTO_ADMIN_EMAIL+SOCIALAUTO_ADMIN_PASSWORD"
        )
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            f"{API_URL}/api/v1/auth/login",
            data={"username": email, "password": password},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        resp.raise_for_status()
        API_TOKEN = resp.json()["access_token"]
    return API_TOKEN


async def _api_request(
    method: str,
    path: str,
    json_body: dict | None = None,
    params: dict | None = None,
) -> dict | list:
    """Make an authenticated API request."""
    token = await _get_token()
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=120) as client:
        if method == "GET":
            resp = await client.get(f"{API_URL}{path}", headers=headers, params=params)
        elif method == "POST":
            headers["Content-Type"] = "application/json"
            resp = await client.post(f"{API_URL}{path}", headers=headers, json=json_body)
        elif method == "DELETE":
            resp = await client.delete(f"{API_URL}{path}", headers=headers)
        elif method == "PATCH":
            headers["Content-Type"] = "application/json"
            resp = await client.patch(f"{API_URL}{path}", headers=headers, json=json_body)
        else:
            raise ValueError(f"Unsupported method: {method}")
        resp.raise_for_status()
        return resp.json() if resp.content else {}


TOOLS: list[Tool] = [
    Tool(
        name="list_accounts",
        description="List all connected social media accounts (LinkedIn, Twitter, Facebook, Instagram, Threads, TikTok)",
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
    Tool(
        name="get_account",
        description="Get details of a specific social account by ID",
        input_schema={
            "type": "object",
            "properties": {"account_id": {"type": "string", "description": "Account UUID"}},
            "required": ["account_id"],
        },
    ),
    Tool(
        name="create_post",
        description="Create a social media post (draft or scheduled)",
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Post text content"},
                "platforms": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Platforms: linkedin, twitter, facebook, instagram, threads, tiktok",
                },
                "scheduled_at": {"type": "string", "description": "ISO 8601 datetime (optional)"},
                "media_ids": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content", "platforms"],
        },
    ),
    Tool(
        name="list_posts",
        description="List social media posts with optional status filter",
        input_schema={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter: draft, scheduled, published, failed"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
        },
    ),
    Tool(
        name="publish_post",
        description="Publish a draft post immediately to all its target platforms",
        input_schema={
            "type": "object",
            "properties": {"post_id": {"type": "string", "description": "Post UUID"}},
            "required": ["post_id"],
        },
    ),
    Tool(
        name="generate_content",
        description="Generate AI content for a social media post",
        input_schema={
            "type": "object",
            "properties": {
                "topic": {"type": "string", "description": "Topic or prompt"},
                "platform": {"type": "string", "description": "Target platform"},
                "tone": {"type": "string", "description": "Tone: professional, casual, etc."},
            },
            "required": ["topic", "platform"],
        },
    ),
    Tool(
        name="suggest_hashtags",
        description="Suggest hashtags for content based on topic and platform",
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Content to generate hashtags for"},
                "platform": {"type": "string", "description": "Target platform"},
                "max_hashtags": {"type": "integer", "description": "Max hashtags (default 5)"},
            },
            "required": ["content", "platform"],
        },
    ),
    Tool(
        name="score_content",
        description="Score content on readability, engagement, hashtag quality, and length fit (no AI call needed)",
        input_schema={
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Post text content"},
                "platform": {"type": "string", "description": "Target platform"},
                "hashtags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["content", "platform"],
        },
    ),
    Tool(
        name="get_analytics",
        description="Get analytics overview for the team",
        input_schema={
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Days to look back (default 30)"},
            },
        },
    ),
    Tool(
        name="list_media",
        description="List media assets in the library",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max results (default 20)"},
                "type": {"type": "string", "description": "Filter: image, video, generated"},
            },
        },
    ),
    Tool(
        name="get_profile",
        description="Get the profile of a connected social account",
        input_schema={
            "type": "object",
            "properties": {"account_id": {"type": "string", "description": "Account UUID"}},
            "required": ["account_id"],
        },
    ),
    Tool(
        name="get_brand",
        description="Get the brand profile (name, tagline, mission, values, visual identity)",
        input_schema={"type": "object", "properties": {}, "required": []},
    ),
]


async def _handle_list_tools(ctx: Any, request: ListToolsRequest) -> ListToolsResult:
    """Handle tools/list request."""
    return ListToolsResult(tools=TOOLS)


async def _handle_call_tool(ctx: Any, request: CallToolRequest) -> CallToolResult:
    """Handle tools/call request."""
    name = request.params.name
    arguments = request.params.arguments or {}

    try:
        if name == "list_accounts":
            result = await _api_request("GET", "/api/v1/accounts")
        elif name == "get_account":
            result = await _api_request("GET", f"/api/v1/accounts/{arguments['account_id']}")
        elif name == "create_post":
            result = await _api_request("POST", "/api/v1/content", json_body=arguments)
        elif name == "list_posts":
            params: dict = {}
            if "status" in arguments:
                params["status"] = arguments["status"]
            if "limit" in arguments:
                params["page_size"] = arguments["limit"]
            result = await _api_request("GET", "/api/v1/content", params=params)
        elif name == "publish_post":
            result = await _api_request("POST", f"/api/v1/content/{arguments['post_id']}/publish")
        elif name == "generate_content":
            result = await _api_request("POST", "/api/v1/ai/generate", json_body=arguments)
        elif name == "suggest_hashtags":
            body = {
                "content": arguments["content"],
                "platform": arguments["platform"],
                "max_hashtags": arguments.get("max_hashtags", 5),
            }
            result = await _api_request("POST", "/api/v1/ai/suggest-hashtags", json_body=body)
        elif name == "score_content":
            body = {
                "content": arguments["content"],
                "platform": arguments["platform"],
                "hashtags": arguments.get("hashtags", []),
            }
            result = await _api_request("POST", "/api/v1/ai/score-content", json_body=body)
        elif name == "get_analytics":
            params = {"days": arguments.get("days", 30)}
            result = await _api_request("GET", "/api/v1/analytics/overview", params=params)
        elif name == "list_media":
            params = {"page_size": arguments.get("limit", 20)}
            if "type" in arguments:
                params["type"] = arguments["type"]
            result = await _api_request("GET", "/api/v1/media/assets", params=params)
        elif name == "get_profile":
            result = await _api_request("GET", f"/api/v1/profile/{arguments['account_id']}")
        elif name == "get_brand":
            result = await _api_request("GET", "/api/v1/brand")
        else:
            return CallToolResult(
                content=[TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}))],
                is_error=True,
            )
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps(result, indent=2, default=str))]
        )
    except httpx.HTTPError as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": str(exc)}))],
            is_error=True,
        )
    except Exception as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=json.dumps({"error": f"{type(exc).__name__}: {exc}"}))],
            is_error=True,
        )


# Register handlers
server.add_request_handler("tools/list", ListToolsRequest, _handle_list_tools)
server.add_request_handler("tools/call", CallToolRequest, _handle_call_tool)


async def main() -> None:
    """Run the MCP server over stdio."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())

# SocialAuto MCP Server

Expose SocialAuto's API through a Model Context Protocol (MCP) server so AI agents
(Claude, ChatGPT, Cursor, Windsurf) can drive content creation, scheduling, and publishing.

Based on research from Hookpost, pendpost, and Posthive — all of which ship MCP servers
for AI agent integration.

## Why MCP?

MCP (Model Context Protocol) is the standard protocol for AI agent tool use. Instead
of pasting API keys or writing custom integrations, AI agents connect to SocialAuto
through an MCP server and can:

- List connected social accounts
- Create and schedule posts
- Generate AI content (copy, hashtags, images)
- Read analytics
- Update profiles
- Manage media library

## Architecture

```
AI Agent (Claude/ChatGPT/Cursor)
    |
    | MCP Protocol (JSON-RPC over stdio or SSE)
    |
SocialAuto MCP Server (Python)
    |
    | HTTP REST API
    |
SocialAuto API (social-api:8083)
```

## Implementation

### MCP Server (app/mcp/server.py)

```python
"""SocialAuto MCP Server — exposes SocialAuto's API to AI agents."""
import json
import os
from mcp.server import Server
from mcp.types import Tool, TextContent

server = Server("socialauto")

SOCIALAUTO_URL = os.environ.get("SOCIALAUTO_URL", "http://localhost:8083")
SOCIALAUTO_TOKEN = os.environ.get("SOCIALAUTO_TOKEN", "")

@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_accounts",
            description="List all connected social media accounts",
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="create_post",
            description="Create a social media post",
            inputSchema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Post text content"},
                    "platforms": {"type": "array", "items": {"type": "string"}},
                    "scheduled_at": {"type": "string", "description": "ISO 8601 datetime"},
                    "media_ids": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["content", "platforms"],
            },
        ),
        Tool(
            name="generate_content",
            description="Generate AI content for a social media post",
            inputSchema={
                "type": "object",
                "properties": {
                    "topic": {"type": "string"},
                    "platform": {"type": "string"},
                    "tone": {"type": "string"},
                },
                "required": ["topic", "platform"],
            },
        ),
        Tool(
            name="get_analytics",
            description="Get analytics for a social account",
            inputSchema={
                "type": "object",
                "properties": {
                    "account_id": {"type": "string"},
                    "days": {"type": "integer", "default": 30},
                },
                "required": ["account_id"],
            },
        ),
        Tool(
            name="list_media",
            description="List media assets in the library",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]

@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    import httpx
    async with httpx.AsyncClient() as client:
        if name == "list_accounts":
            resp = await client.get(
                f"{SOCIALAUTO_URL}/api/v1/accounts",
                headers={"Authorization": f"Bearer {SOCIALAUTO_TOKEN}"},
            )
        elif name == "create_post":
            resp = await client.post(
                f"{SOCIALAUTO_URL}/api/v1/content",
                headers={"Authorization": f"Bearer {SOCIALAUTO_TOKEN}"},
                json=arguments,
            )
        elif name == "generate_content":
            resp = await client.post(
                f"{SOCIALAUTO_URL}/api/v1/ai/generate",
                headers={"Authorization": f"Bearer {SOCIALAUTO_TOKEN}"},
                json=arguments,
            )
        elif name == "get_analytics":
            resp = await client.get(
                f"{SOCIALAUTO_URL}/api/v1/analytics/account/{arguments['account_id']}",
                headers={"Authorization": f"Bearer {SOCIALAUTO_TOKEN}"},
                params={"days": arguments.get("days", 30)},
            )
        elif name == "list_media":
            resp = await client.get(
                f"{SOCIALAUTO_URL}/api/v1/media",
                headers={"Authorization": f"Bearer {SOCIALAUTO_TOKEN}"},
            )
        return [TextContent(type="text", text=json.dumps(resp.json(), indent=2))]
```

### Docker Compose Service

```yaml
socialauto-mcp:
  build: ./social-automation/backend
  command: python -m app.mcp.server
  environment:
    SOCIALAUTO_URL: http://social-api:8083
    SOCIALAUTO_TOKEN: ${SOCIALAUTO_MCP_TOKEN}
  depends_on:
    - social-api
```

### Devin CLI MCP Config

```json
// .devin/mcp_config.json
{
  "mcpServers": {
    "socialauto": {
      "command": "python",
      "args": ["-m", "app.mcp.server"],
      "env": {
        "SOCIALAUTO_URL": "http://localhost:8083",
        "SOCIALAUTO_TOKEN": "<admin_token>"
      }
    }
  }
}
```

## Free/Open-Source Tools Referenced

- **Hookpost**: https://github.com/jatinder14/hookpost — MCP server + CLI for social media
  scheduling (AGPL-3.0)
- **pendpost**: https://github.com/pendpost/pendpost — MCP-native social media planner with
  human approval gate (MIT)
- **Posthive**: https://github.com/AstaBlackClove/posthive — MCP server with OAuth 2.0 + PKCE
  (AGPL-3.0)

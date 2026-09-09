"""Unit tests for MCP server."""
from unittest.mock import AsyncMock, patch

import httpx
import pytest

pytest.importorskip("mcp")
from mcp.types import CallToolRequest, CallToolRequestParams, ListToolsRequest

from app.mcp.server import TOOLS, _handle_call_tool, _handle_list_tools


class TestTools:
    def test_tool_count(self):
        assert len(TOOLS) == 12

    def test_required_tools_present(self):
        names = {t.name for t in TOOLS}
        required = {
            "list_accounts", "get_account", "create_post", "list_posts",
            "publish_post", "generate_content", "suggest_hashtags",
            "score_content", "get_analytics", "list_media",
            "get_profile", "get_brand",
        }
        assert required.issubset(names)

    def test_tool_has_input_schema(self):
        for tool in TOOLS:
            assert "type" in tool.input_schema
            assert tool.input_schema["type"] == "object"

    def test_tool_has_description(self):
        for tool in TOOLS:
            assert len(tool.description) > 10


class TestHandleListTools:
    @pytest.mark.asyncio
    async def test_returns_all_tools(self):
        result = await _handle_list_tools(ListToolsRequest(method="tools/list"))
        assert len(result.tools) == 12


class TestHandleCallTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self):
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="unknown_tool", arguments={}),
        )
        result = await _handle_call_tool(request)
        assert result.is_error is True

    @pytest.mark.asyncio
    async def test_list_accounts(self):
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="list_accounts", arguments={}),
        )
        with patch("app.mcp.server._api_request", new=AsyncMock(return_value=[])):
            result = await _handle_call_tool(request)
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_score_content(self):
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(
                name="score_content",
                arguments={"content": "test post", "platform": "linkedin"},
            ),
        )
        mock_response = {"readability": 80.0, "engagement": 70.0, "overall": 75.0}
        with patch("app.mcp.server._api_request", new=AsyncMock(return_value=mock_response)):
            result = await _handle_call_tool(request)
        assert not result.is_error

    @pytest.mark.asyncio
    async def test_api_error_handled(self):
        request = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="list_accounts", arguments={}),
        )
        with patch("app.mcp.server._api_request", new=AsyncMock(side_effect=httpx.ConnectError("refused"))):
            result = await _handle_call_tool(request)
        assert result.is_error is True


class TestGetToken:
    @pytest.mark.asyncio
    async def test_returns_env_token(self):
        import app.mcp.server as srv
        srv.API_TOKEN = "test-token"
        token = await srv._get_token()
        assert token == "test-token"

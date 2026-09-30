"""Regression tests for the ensure_session verified-dead marker.

A "not logged in" probe burns ~200s of bridge busy-hold. Dead platforms
re-paid that every poll cycle, saturating the bridge so the hourly session
healer could never acquire it. The Redis dead marker makes repeat polls
skip fast; a live session (healer or noVNC login) is never hidden because
the cheap status checks run before the marker is consulted.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.services.browser_bridge import BrowserBridgeClient


def _client():
    return BrowserBridgeClient("http://bridge:9223")


class TestDeadMarker:
    @pytest.mark.asyncio
    async def test_marker_skips_expensive_probe(self):
        client = _client()
        client.session_status = AsyncMock(return_value={"status": "idle"})
        client.start_session = AsyncMock()
        with (
            patch.object(client, "_dead_marker_get", AsyncMock(return_value="1")),
            patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
        ):
            post.return_value.status_code = 404  # extract probe finds nothing
            result = await client.ensure_session("threads")
        assert result["status"] == "waiting"
        client.start_session.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_verified_dead_sets_marker(self):
        client = _client()
        waiting = {
            "status": "waiting",
            "platform": "threads",
            "cookies_found": [],
        }
        client.session_status = AsyncMock(return_value=waiting)
        client.start_session = AsyncMock()
        set_marker = AsyncMock()
        with (
            patch.object(client, "_dead_marker_get", AsyncMock(return_value=None)),
            patch.object(client, "_dead_marker_set", set_marker),
            patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
            patch.object(asyncio, "sleep", new_callable=AsyncMock),
        ):
            post.return_value.status_code = 404
            result = await client.ensure_session("threads")
        assert result["status"] == "waiting"
        # detection window ran to completion on the platform's own session
        client.start_session.assert_awaited_once()
        set_marker.assert_awaited_once_with("threads")

    @pytest.mark.asyncio
    async def test_contended_probe_does_not_mark(self):
        from app.services.browser_bridge import BrowserBridgeError

        client = _client()
        client.session_status = AsyncMock(return_value={"status": "idle"})
        client.start_session = AsyncMock(side_effect=BrowserBridgeError(409, "busy"))
        set_marker = AsyncMock()
        with (
            patch.object(client, "_dead_marker_get", AsyncMock(return_value=None)),
            patch.object(client, "_dead_marker_set", set_marker),
            patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post,
        ):
            post.return_value.status_code = 404
            result = await client.ensure_session("threads")
        # never verified — contention must not poison the marker
        assert result["status"] == "waiting"
        set_marker.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_live_session_bypasses_marker(self):
        client = _client()
        client.session_status = AsyncMock(
            return_value={
                "status": "done",
                "platform": "threads",
                "cookies_found": ["sessionid"],
            }
        )
        with patch.object(client, "_dead_marker_get", AsyncMock(return_value="1")):
            result = await client.ensure_session("threads")
        # cheap check wins — a healed session is never hidden by the marker
        assert result["status"] == "active"

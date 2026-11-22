"""Regression tests: instagram_lane_guard._token_alive must be tri-state.

A transport failure (DNS/timeout) is inconclusive — it must NOT read as
a dead token, or every sweep would false-alert "business_login token
dead" and could repoint targets off a healthy lane.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.worker.tasks import instagram_lane_guard as guard


def _account():
    return SimpleNamespace(id="acc-1", access_token_enc=b"enc", username="cloudless.gr")


def _resp(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://graph.instagram.com/v26.0/me"))


@pytest.mark.asyncio
async def test_alive_on_200():
    with (
        patch.object(guard, "decrypt_token", return_value="tok"),
        patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=_resp(200))),
    ):
        assert await guard._token_alive(_account()) is True


@pytest.mark.asyncio
async def test_dead_on_401():
    with (
        patch.object(guard, "decrypt_token", return_value="tok"),
        patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=_resp(401))),
    ):
        assert await guard._token_alive(_account()) is False


@pytest.mark.asyncio
async def test_inconclusive_on_transport_error():
    with (
        patch.object(guard, "decrypt_token", return_value="tok"),
        patch.object(
            httpx.AsyncClient,
            "get",
            new=AsyncMock(side_effect=httpx.ConnectError("dns failure")),
        ),
    ):
        assert await guard._token_alive(_account()) is None


@pytest.mark.asyncio
async def test_inconclusive_on_5xx():
    with (
        patch.object(guard, "decrypt_token", return_value="tok"),
        patch.object(httpx.AsyncClient, "get", new=AsyncMock(return_value=_resp(503))),
    ):
        assert await guard._token_alive(_account()) is None


@pytest.mark.asyncio
async def test_undecryptable_credential_is_dead():
    with patch.object(guard, "decrypt_token", side_effect=ValueError("bad key")):
        assert await guard._token_alive(_account()) is False

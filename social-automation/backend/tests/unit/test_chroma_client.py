"""Unit tests for ChromaDB client embedding fallback (DMR → Cloudflare BGE-M3 → Ollama)."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import chroma_client


@pytest.mark.asyncio
async def test_cf_embedding_success():
    """Cloudflare BGE-M3 returns embeddings in result.data[0]."""

    class FakeResp:
        status_code = 200

        def json(self):
            return {"result": {"data": [[0.1, 0.2, 0.3]], "shape": [1, 3]}}

    with (
        patch.object(chroma_client, "_cf_ai_token", return_value="tok-123"),
        patch.object(chroma_client.settings, "CLOUDFLARE_ACCOUNT_ID", "acc-123"),
        patch("app.services.chroma_client.httpx.AsyncClient") as mock_client_cls,
    ):
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=FakeResp())
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client_cls.return_value = mock_client

        embedding = await chroma_client._cf_embedding("test text")
    assert embedding == [0.1, 0.2, 0.3]


@pytest.mark.asyncio
async def test_cf_embedding_no_credentials():
    """Returns empty list when Cloudflare credentials are missing."""
    with (
        patch.object(chroma_client, "_cf_ai_token", return_value=""),
        patch.object(chroma_client.settings, "CLOUDFLARE_ACCOUNT_ID", ""),
    ):
        embedding = await chroma_client._cf_embedding("test text")
    assert embedding == []


@pytest.mark.asyncio
async def test_get_embedding_dmr_fails_cf_fallback():
    """_get_embedding tries DMR first, then Cloudflare when DMR fails."""
    with (
        patch.object(chroma_client, "_dmr_embedding", new=AsyncMock(return_value=[])),
        patch.object(chroma_client, "_cf_embedding", new=AsyncMock(return_value=[0.4, 0.5])),
    ):
        result = await chroma_client._get_embedding("test")
    assert result == [0.4, 0.5]


@pytest.mark.asyncio
async def test_get_embedding_dmr_success_no_cf():
    """When DMR succeeds, Cloudflare is not called."""
    with (
        patch.object(chroma_client, "_dmr_embedding", new=AsyncMock(return_value=[0.1, 0.2])),
        patch.object(chroma_client, "_cf_embedding", new=AsyncMock(side_effect=AssertionError("should not be called"))),
    ):
        result = await chroma_client._get_embedding("test")
    assert result == [0.1, 0.2]

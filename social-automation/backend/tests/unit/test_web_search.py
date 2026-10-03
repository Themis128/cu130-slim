"""Unit tests for app.services.web_search — SearXNG client.

httpx transport is mocked; no network.
"""
import httpx
import pytest

from app.services import web_search as ws

_REAL_CLIENT = httpx.AsyncClient


def _mock_client(handler):
    """Return an httpx.AsyncClient wired to MockTransport — patched in place
    of the real client factory used by web_search."""
    def factory(*args, **kwargs):
        return _REAL_CLIENT(
            transport=httpx.MockTransport(handler), timeout=kwargs.get("timeout")
        )
    return factory


def _payload(n=3):
    return {
        "results": [
            {
                "title": f"Result {i}",
                "url": f"https://example.com/{i}",
                "content": f"Snippet text {i}",
                "engine": "duckduckgo",
            }
            for i in range(n)
        ]
    }


@pytest.mark.asyncio
async def test_returns_normalized_results(monkeypatch):
    monkeypatch.setattr(
        ws.httpx, "AsyncClient",
        _mock_client(lambda req: httpx.Response(200, json=_payload(8))),
    )
    monkeypatch.setattr(ws.settings, "SEARXNG_URL", "http://searxng:8080")
    results = await ws.web_search("self-hosted tools", limit=5)
    assert len(results) == 5  # limit applied
    assert results[0]["title"] == "Result 0"
    assert results[0]["url"] == "https://example.com/0"
    assert results[0]["snippet"] == "Snippet text 0"
    assert results[0]["engine"] == "duckduckgo"


@pytest.mark.asyncio
async def test_empty_query_returns_empty():
    assert await ws.web_search("") == []
    assert await ws.web_search("   ") == []


@pytest.mark.asyncio
async def test_unconfigured_url_returns_empty(monkeypatch):
    monkeypatch.setattr(ws.settings, "SEARXNG_URL", "")
    assert await ws.web_search("anything") == []


@pytest.mark.asyncio
async def test_http_error_returns_empty(monkeypatch):
    monkeypatch.setattr(
        ws.httpx, "AsyncClient",
        _mock_client(lambda req: httpx.Response(500, text="boom")),
    )
    monkeypatch.setattr(ws.settings, "SEARXNG_URL", "http://searxng:8080")
    assert await ws.web_search("query") == []


@pytest.mark.asyncio
async def test_timeout_returns_empty(monkeypatch):
    def handler(req):
        raise httpx.ConnectTimeout("slow")

    monkeypatch.setattr(ws.httpx, "AsyncClient", _mock_client(handler))
    monkeypatch.setattr(ws.settings, "SEARXNG_URL", "http://searxng:8080")
    assert await ws.web_search("query") == []


@pytest.mark.asyncio
async def test_skips_results_without_url_and_caps(monkeypatch):
    payload = _payload(3)
    payload["results"].insert(0, {"title": "no url", "content": "x"})
    monkeypatch.setattr(
        ws.httpx, "AsyncClient",
        _mock_client(lambda req: httpx.Response(200, json=payload)),
    )
    monkeypatch.setattr(ws.settings, "SEARXNG_URL", "http://searxng:8080")
    results = await ws.web_search("q", limit=50)
    assert len(results) == 3  # url-less entry dropped, cap at MAX_LIMIT applied
    assert all(r["url"] for r in results)


def test_format_search_context():
    ctx = ws.format_search_context(
        [{"title": "T", "url": "https://x.y", "snippet": "s", "engine": "e"}], "q"
    )
    assert "RECENT WEB RESULTS" in ctx
    assert "https://x.y" in ctx
    assert ws.format_search_context([], "q") == ""

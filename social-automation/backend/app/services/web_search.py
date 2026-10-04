"""Web search via self-hosted SearXNG — free, no API keys.

SearXNG runs as a lightweight container (see ``deploy/omv-workspace`` — it is
deployed on the omv node and reached over the LAN). Its JSON API
(``/search?format=json``) aggregates public engines, giving content generation
fresh, grounded context: current events, trends, source links for hooks and
hashtags — without the paid Cloudflare Web Search / Tavily pricing.

Fail-soft by design: every failure mode (unreachable, timeout, non-JSON,
rate-limited) returns ``[]`` so callers can degrade gracefully — generation
must never be blocked by search availability.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 5
MAX_LIMIT = 10


def _sanitize_log_text(text: str, max_len: int = 200) -> str:
    """Strip newlines/control chars from user-derived text before logging."""
    cleaned = text.replace("\n", "\\n").replace("\r", "\\r")
    cleaned = "".join(c for c in cleaned if c == "\t" or ord(c) >= 0x20)
    return cleaned[:max_len]


async def web_search(
    query: str,
    limit: int = DEFAULT_LIMIT,
    *,
    categories: str | None = None,
    timeout: float = 8.0,
) -> list[dict[str, Any]]:
    """Query SearXNG and return the top ``limit`` results.

    Each result is ``{"title", "url", "snippet", "engine"}``. Returns ``[]``
    on any failure — callers should treat an empty list as "no context".
    """
    query = (query or "").strip()
    if not query or not settings.SEARXNG_URL:
        return []
    limit = max(1, min(limit, MAX_LIMIT))

    params: dict[str, str] = {"q": query, "format": "json"}
    if categories:
        params["categories"] = categories

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(
                f"{settings.SEARXNG_URL.rstrip('/')}/search", params=params
            )
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001 — fail-soft, never block generation
        safe_q = query[:80].replace("\n", "\\n").replace("\r", "\\r")
        safe_e = str(exc).replace("\n", "\\n").replace("\r", "\\r")
        logger.warning("SearXNG search failed for %r: %s", safe_q, safe_e)
        return []

    results: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        url = item.get("url")
        if not url:
            continue
        results.append(
            {
                "title": (item.get("title") or "").strip(),
                "url": url,
                "snippet": (item.get("content") or "").strip(),
                "engine": item.get("engine") or "",
            }
        )
        if len(results) >= limit:
            break
    return results


def format_search_context(results: list[dict[str, Any]], query: str) -> str:
    """Render search results as a prompt context block."""
    if not results:
        return ""
    lines = [
        f'RECENT WEB RESULTS for "{query}" (use for fresh facts, trends and '
        "angles — cite source names in prose when relevant, never raw URLs):"
    ]
    for r in results:
        lines.append(f"- {r['title']} — {r['url']}\n  {r['snippet'][:280]}")
    return "\n".join(lines)

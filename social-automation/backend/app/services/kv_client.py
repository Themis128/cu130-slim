"""Cloudflare KV client for cache and queue operations.

Used as the primary cache layer with Redis as local failover.
Free tier: 100K reads/day, 1K writes/day, 1GB storage.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class KVClient:
    """Async client for Cloudflare Workers KV REST API."""

    def __init__(self) -> None:
        self.account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
        self.api_token = (settings.CLOUDFLARE_API_TOKEN or "").strip()
        self.cache_ns = (settings.KV_CACHE_NAMESPACE or "").strip()
        self._base_url: str | None = None
        self._enabled: bool | None = None

    @property
    def enabled(self) -> bool:
        if self._enabled is None:
            self._enabled = all([self.account_id, self.api_token, self.cache_ns])
        return self._enabled

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = (
                f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
                f"/storage/kv/namespaces/{self.cache_ns}"
            )
        return self._base_url

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
        }

    async def get(self, key: str) -> str | None:
        """Get a value by key. Returns None if not found."""
        if not self.enabled:
            return None
        url = f"{self.base_url}/values/{key}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url, headers=self._headers())
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.text

    async def get_json(self, key: str) -> Any | None:
        """Get and JSON-decode a value."""
        raw = await self.get(key)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    async def put(self, key: str, value: str, expiration_ttl: int | None = None) -> bool:
        """Store a value. Returns True on success."""
        if not self.enabled:
            return False
        url = f"{self.base_url}/values/{key}"
        params = {}
        if expiration_ttl:
            params["expiration_ttl"] = expiration_ttl
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(url, content=value, headers=self._headers(), params=params)
        return resp.status_code == 200

    async def put_json(self, key: str, value: Any, expiration_ttl: int | None = None) -> bool:
        """JSON-encode and store a value."""
        return await self.put(key, json.dumps(value), expiration_ttl)

    async def delete(self, key: str) -> bool:
        """Delete a key. Returns True on success."""
        if not self.enabled:
            return False
        url = f"{self.base_url}/values/{key}"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(url, headers=self._headers())
        return resp.status_code == 200

    async def list_keys(self, prefix: str = "", limit: int = 100) -> list[str]:
        """List keys with optional prefix filter."""
        if not self.enabled:
            return []
        keys: list[str] = []
        cursor = ""
        url = f"{self.base_url}/keys"
        async with httpx.AsyncClient(timeout=10) as client:
            while True:
                # KV API requires limit >= 1, but some endpoints need >= 100
                params = {"limit": max(limit, 100)}
                if prefix:
                    params["prefix"] = prefix
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get(url, headers=self._headers(), params=params)
                if resp.status_code != 200:
                    break
                data = resp.json()
                if not data.get("success"):
                    break
                result = data.get("result", [])
                for item in result:
                    keys.append(item.get("name", ""))
                if len(keys) >= limit:
                    break
                cursor_info = data.get("result_info", {})
                cursor = cursor_info.get("cursor", "")
                if not cursor:
                    break
        return keys[:limit]

    async def health(self) -> bool:
        """Check if KV is reachable by doing a simple get on a health key."""
        if not self.enabled:
            return False
        try:
            # Use put + get on a health key instead of list (list has stricter limits)
            await self.put("__health__", "ok", expiration_ttl=60)
            val = await self.get("__health__")
            return val == "ok"
        except Exception:
            return False


# Singleton instance
kv_client = KVClient()

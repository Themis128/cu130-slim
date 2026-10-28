"""Cloudflare Vectorize client for vector embeddings.

Used as the primary vector database with ChromaDB as local failover.
Free tier: 30M queried dimensions/month, 10M stored dimensions.

The index "social-embeddings" is configured with 1024 dimensions and cosine metric
to match Cloudflare BGE-M3 embeddings.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class VectorizeClient:
    """Async client for Cloudflare Vectorize REST API."""

    def __init__(self) -> None:
        self.account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
        self.index_name = (settings.VECTORIZE_INDEX_NAME or "social-embeddings").strip()
        # Token fallback chain: API token → AI token → Email token
        self._tokens = [
            t for t in [
                (settings.CLOUDFLARE_API_TOKEN or "").strip(),
                (settings.CLOUDFLARE_AI_API_TOKEN or "").strip(),
                (getattr(settings, "CLOUDFLARE_EMAIL_API_TOKEN", "") or "").strip(),
            ]
            if t
        ]
        self.api_token = self._tokens[0] if self._tokens else ""
        self._active_token: str | None = None
        self._base_url: str | None = None
        self._enabled: bool | None = None

    @property
    def enabled(self) -> bool:
        if self._enabled is None:
            self._enabled = all([self.account_id, self.api_token, self.index_name])
        return self._enabled

    @property
    def base_url(self) -> str:
        if self._base_url is None:
            self._base_url = (
                f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}"
                f"/vectorize/v2/indexes/{self.index_name}"
            )
        return self._base_url

    def _headers(self) -> dict[str, str]:
        token = self._active_token or self.api_token
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def _try_tokens(self, method, url, **kwargs) -> httpx.Response:
        """Try request with each available token until one works (not 401)."""
        last_resp = None
        for token in self._tokens:
            self._active_token = token
            headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
            try:
                resp = await method(url, headers=headers, **kwargs)
                if resp.status_code != 401:
                    return resp
                last_resp = resp
                logger.warning("Vectorize token failed (401), trying next token...")
            except Exception:
                continue
        self._active_token = None
        if last_resp:
            return last_resp
        raise RuntimeError("All Cloudflare tokens failed for Vectorize")

    async def upsert(
        self,
        id: str,
        values: list[float],
        namespace: str = "default",
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Insert or update a vector.

        Args:
            id: Unique vector ID.
            values: Embedding vector (must match index dimensions, 1024 for BGE-M3).
            namespace: Optional namespace for partitioning.
            metadata: Optional metadata to store alongside the vector.

        Returns:
            True on success.
        """
        if not self.enabled:
            return False
        url = f"{self.base_url}/upsert"
        body: dict[str, Any] = {
            "vectors": [
                {
                    "id": id,
                    "values": values,
                    "namespace": namespace,
                }
            ]
        }
        if metadata:
            body["vectors"][0]["metadata"] = metadata
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await self._try_tokens(client.post, url, json=body)
        return resp.status_code == 200

    async def upsert_many(
        self,
        vectors: list[dict[str, Any]],
        namespace: str = "default",
    ) -> int:
        """Upsert multiple vectors. Each vector dict must have id, values, and optional metadata.

        Returns count of successful upserts.
        """
        if not self.enabled:
            return 0
        url = f"{self.base_url}/upsert"
        # Add namespace to each vector if not present
        for v in vectors:
            v.setdefault("namespace", namespace)
        body = {"vectors": vectors}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await self._try_tokens(client.post, url, json=body)
        if resp.status_code == 200:
            return len(vectors)
        logger.error("Vectorize upsert_many failed: %s", resp.text[:200])
        return 0

    async def query(
        self,
        values: list[float],
        top_k: int = 5,
        namespace: str = "default",
        return_metadata: bool = True,
    ) -> list[dict[str, Any]]:
        """Query for similar vectors.

        Args:
            values: Query embedding vector.
            top_k: Number of results to return.
            namespace: Namespace to search within.
            return_metadata: Whether to include metadata in results.

        Returns:
            List of matches, each with id, score, and optionally metadata.
        """
        if not self.enabled:
            return []
        url = f"{self.base_url}/query"
        body: dict[str, Any] = {
            "vector": values,
            "topK": top_k,
            "namespace": namespace,
            "return_metadata": return_metadata,
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await self._try_tokens(client.post, url, json=body)
        if resp.status_code != 200:
            logger.error("Vectorize query failed: %s", resp.text[:200])
            return []
        data = resp.json()
        if not data.get("success"):
            return []
        result = data.get("result", {})
        return result.get("matches", [])

    async def get_vectors(self, ids: list[str], namespace: str = "default") -> list[dict[str, Any]]:
        """Retrieve vectors by ID."""
        if not self.enabled:
            return []
        url = f"{self.base_url}/get"
        body = {"ids": ids, "namespace": namespace}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await self._try_tokens(client.post, url, json=body)
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data.get("result", {}).get("vectors", [])

    async def delete_vectors(self, ids: list[str], namespace: str = "default") -> bool:
        """Delete vectors by ID."""
        if not self.enabled:
            return False
        url = f"{self.base_url}/delete_by_ids"
        body = {"ids": ids}
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await self._try_tokens(client.post, url, json=body)
        return resp.status_code == 200

    async def health(self) -> bool:
        """Check if Vectorize index is reachable."""
        if not self.enabled:
            return False
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{self.account_id}/vectorize/v2/indexes"
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await self._try_tokens(client.get, url)
            data = resp.json()
            if data.get("success"):
                indexes = data.get("result", [])
                return any(idx.get("name") == self.index_name for idx in indexes)
            return False
        except Exception as exc:
            logger.warning("Vectorize health check failed: %s", exc)
            return False


# Singleton instance
vectorize_client = VectorizeClient()

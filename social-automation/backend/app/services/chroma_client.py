"""Thin async client for ChromaDB's HTTP API (v2).

Embeddings use Cloudflare Workers AI BGE-M3 (multilingual, 0 neurons) as the
primary provider with Ollama as the last-resort fallback, per the Cloudflare-
first, Ollama-last-resort product policy.
"""
import httpx

from app.core.config import get_settings
from app.services.cf_models import CF_EMBEDDING_MULTILINGUAL

settings = get_settings()

_EMBED_TIMEOUT = 30.0
_CHROMA_TIMEOUT = 10.0

# Chroma v2 API base path (Chroma 1.x uses tenant/database structure)
_CHROMA_API_BASE = "/api/v2/tenants/default_tenant/databases/default_database"


def _cf_ai_token() -> str:
    """Return the Workers AI token (prefers CLOUDFLARE_AI_API_TOKEN)."""
    return (settings.CLOUDFLARE_AI_API_TOKEN or "").strip() or (settings.CLOUDFLARE_API_TOKEN or "").strip()


async def _cf_embedding(text: str) -> list[float]:
    """Call Cloudflare Workers AI BGE-M3 for embeddings. Returns [] on failure."""
    account_id = (settings.CLOUDFLARE_ACCOUNT_ID or "").strip()
    token = _cf_ai_token()
    if not account_id or not token:
        return []
    url = f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{CF_EMBEDDING_MULTILINGUAL}"
    async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
        try:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json={"text": [text]},
            )
            if resp.status_code == 200:
                data = resp.json()
                # Workers AI returns {"result": {"data": [[...]], "shape": [1, 1024]}}
                result = data.get("result") or data
                if isinstance(result, dict):
                    embeddings = result.get("data") or result.get("embeddings")
                    if embeddings and isinstance(embeddings, list):
                        return embeddings[0] if isinstance(embeddings[0], list) else embeddings
        except Exception:
            pass
    return []


async def _ollama_embedding(text: str) -> list[float]:
    """Call Ollama for embeddings (last-resort fallback). Returns [] on failure."""
    async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{settings.OLLAMA_URL}/api/embeddings",
                json={"model": settings.OLLAMA_EMBEDDING_MODEL, "prompt": text},
            )
            if resp.status_code == 200:
                return resp.json().get("embedding", [])
        except Exception:
            pass
    return []


async def _get_embedding(text: str) -> list[float]:
    """Embed text via Cloudflare BGE-M3 first, then Ollama as fallback."""
    embedding = await _cf_embedding(text)
    if embedding:
        return embedding
    return await _ollama_embedding(text)


def _collection_name(team_id: str) -> str:
    return f"team_{team_id.replace('-', '_')}_content"


def _collection_base_url() -> str:
    return f"{settings.CHROMA_URL}{_CHROMA_API_BASE}/collections"


async def _get_collection_id(client: httpx.AsyncClient, col: str) -> str | None:
    """Get collection UUID by name, creating if needed."""
    try:
        # Try to get existing collection
        resp = await client.get(f"{_collection_base_url()}/{col}")
        if resp.status_code == 200:
            return resp.json().get("id")
    except Exception:
        pass

    # Create collection
    try:
        resp = await client.post(
            _collection_base_url(),
            json={"name": col, "get_or_create": True},
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id")
    except Exception:
        pass
    return None


async def add_content(team_id: str, post_id: str, text: str) -> None:
    """Embed text and store in the team's ChromaDB collection."""
    embedding = await _get_embedding(text)
    if not embedding:
        return

    col = _collection_name(team_id)
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col)
            if not col_id:
                return
            await client.post(
                f"{_collection_base_url()}/{col_id}/add",
                json={"ids": [post_id], "embeddings": [embedding], "documents": [text]},
            )
        except Exception:
            pass  # chroma unavailable; don't block the request


async def get_content(team_id: str, post_id: str) -> str | None:
    """Fetch a document by id from the team's ChromaDB collection."""
    col = _collection_name(team_id)
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col)
            if not col_id:
                return None
            resp = await client.post(
                f"{_collection_base_url()}/{col_id}/get",
                json={"ids": [post_id], "include": ["documents"]},
            )
            if resp.status_code == 200:
                docs = resp.json().get("documents", [])
                if docs:
                    return docs[0]
        except Exception:
            pass
    return None


async def query_similar(team_id: str, text: str, n_results: int = 5) -> list[str]:
    """Return up to n_results similar documents from the team's collection."""
    embedding = await _get_embedding(text)
    if not embedding:
        return []

    col = _collection_name(team_id)
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            col_id = await _get_collection_id(client, col)
            if not col_id:
                return []
            resp = await client.post(
                f"{_collection_base_url()}/{col_id}/query",
                json={"query_embeddings": [embedding], "n_results": n_results},
            )
            if resp.status_code == 200:
                return resp.json().get("documents", [[]])[0]
        except Exception:
            pass
    return []

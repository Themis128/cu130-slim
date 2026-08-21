"""Thin async client for ChromaDB's HTTP API and Ollama embeddings."""
import httpx
from app.core.config import get_settings

settings = get_settings()

_EMBED_TIMEOUT = 30.0
_CHROMA_TIMEOUT = 10.0


async def _get_embedding(text: str) -> list[float]:
    async with httpx.AsyncClient(timeout=_EMBED_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{settings.OLLAMA_URL}/api/embeddings",
                json={"model": settings.OLLAMA_DEFAULT_MODEL, "prompt": text},
            )
            if resp.status_code == 200:
                return resp.json().get("embedding", [])
        except Exception:
            pass
    return []


def _collection_name(team_id: str) -> str:
    return f"team_{team_id.replace('-', '_')}_content"


async def add_content(team_id: str, post_id: str, text: str) -> None:
    """Embed text and store in the team's ChromaDB collection."""
    embedding = await _get_embedding(text)
    if not embedding:
        return

    col = _collection_name(team_id)
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            await client.post(
                f"{settings.CHROMA_URL}/api/v1/collections",
                json={"name": col, "get_or_create": True},
            )
            await client.post(
                f"{settings.CHROMA_URL}/api/v1/collections/{col}/add",
                json={"ids": [post_id], "embeddings": [embedding], "documents": [text]},
            )
        except Exception:
            pass  # chroma unavailable; don't block the request


async def query_similar(team_id: str, text: str, n_results: int = 5) -> list[str]:
    """Return up to n_results similar documents from the team's collection."""
    embedding = await _get_embedding(text)
    if not embedding:
        return []

    col = _collection_name(team_id)
    async with httpx.AsyncClient(timeout=_CHROMA_TIMEOUT) as client:
        try:
            resp = await client.post(
                f"{settings.CHROMA_URL}/api/v1/collections/{col}/query",
                json={"query_embeddings": [embedding], "n_results": n_results},
            )
            if resp.status_code == 200:
                return resp.json().get("documents", [[]])[0]
        except Exception:
            pass
    return []

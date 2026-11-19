"""Interview-coach proxy — fronts the interview-coach CF worker.

The voice ML-interview coach lives in a standalone Cloudflare Worker
(``interview-coach.<acct>.workers.dev``) holding its own D1 ``interview_db``,
Vectorize ``cv-index`` and VoiceAgent Durable Object. This router exposes the
same surface inside SocialAuto behind normal user auth so the console can
drive it without handling the worker ``AUTH_SECRET``.

Worker endpoints proxied (key passed as ``x-key``, never in the URL):
  POST /seed          {texts[]}            -> CV facts -> Vectorize
  POST /transcribe    audio bytes          -> STT fallback chain
  POST /classify      {text}               -> interviewer|candidate
  POST /chat          OpenAI-style body    -> /engines/chat/completions (+CV RAG)
  POST /tts           {text}               -> audio/mpeg
  POST /log           {session,who,text}   -> D1 turns
  GET  /latest        last candidate answer
  GET  /history       last 300 turns

Not proxied: ``/stt`` WebSocket (Durable Object lane — needs a WS proxy,
deferred) and arbitrary ``/engines/*`` paths (only chat/completions is used).
"""

from __future__ import annotations

import json
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.models.user import User

logger = logging.getLogger(__name__)
router = APIRouter()

CurrentUser = Depends(get_current_user)


def _config() -> tuple[str, str]:
    s = get_settings()
    url = (getattr(s, "INTERVIEW_COACH_URL", "") or "").strip().rstrip("/")
    key = (getattr(s, "INTERVIEW_COACH_KEY", "") or "").strip()
    return url, key


def _client(timeout: float) -> httpx.AsyncClient:
    url, key = _config()
    if not url or not key:
        raise HTTPException(
            503, "interview-coach not configured "
            "(INTERVIEW_COACH_URL/INTERVIEW_COACH_KEY)"
        )
    return httpx.AsyncClient(base_url=url, headers={"x-key": key}, timeout=timeout)


async def _proxy(
    method: str,
    path: str,
    *,
    timeout: float = 30.0,
    content: bytes | None = None,
    json_body: dict | None = None,
    content_type: str | None = None,
) -> Response:
    headers = {}
    if content_type:
        headers["Content-Type"] = content_type
    try:
        async with _client(timeout) as client:
            resp = await client.request(
                method, path, content=content, json=json_body, headers=headers
            )
    except httpx.HTTPError as exc:
        logger.warning("interview-coach %s %s failed: %s", method, path, exc)
        raise HTTPException(502, f"interview-coach upstream error: {exc}") from exc
    media = resp.headers.get("content-type", "application/json")
    if "audio/" in media or "text/event-stream" in media:
        # binary audio + SSE bodies pass through with their media type
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            media_type=media.split(";")[0],
        )
    try:
        payload = resp.json()
    except ValueError:
        payload = {"raw": resp.text[:2000]}
    return Response(
        content=json.dumps(payload),
        status_code=resp.status_code,
        media_type="application/json",
    )


# --- ingestion ---------------------------------------------------------------


class SeedIn(BaseModel):
    texts: list[str] = Field(..., min_length=1, max_length=200)


@router.post("/seed")
async def seed(body: SeedIn, _: User = CurrentUser):
    return await _proxy("POST", "/seed", json_body=body.model_dump())


@router.post("/transcribe")
async def transcribe(request: Request, _: User = CurrentUser):
    audio = await request.body()
    if not audio:
        raise HTTPException(400, "audio body required")
    if len(audio) > 25 * 1024 * 1024:  # worker MAX_AUDIO_BYTES guard
        raise HTTPException(413, "audio too large")
    return await _proxy(
        "POST", "/transcribe", timeout=60.0,
        content=audio, content_type="application/octet-stream",
    )


# --- turn classification -----------------------------------------------------


class ClassifyIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=8000)


@router.post("/classify")
async def classify(body: ClassifyIn, _: User = CurrentUser):
    return await _proxy("POST", "/classify", json_body=body.model_dump())


# --- coach chat (OpenAI-style, RAG over seeded CV) ----------------------------


class ChatIn(BaseModel):
    messages: list[dict] = Field(..., min_length=1)
    model: str | None = None
    stream: bool = False
    max_tokens: int | None = None
    rag: bool | None = None
    lora: str | None = None


@router.post("/chat")
async def chat(body: ChatIn, _: User = CurrentUser):
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    return await _proxy(
        "POST", "/engines/chat/completions", timeout=90.0, json_body=payload
    )


# --- voice out ---------------------------------------------------------------


class TtsIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


@router.post("/tts")
async def tts(body: TtsIn, _: User = CurrentUser):
    return await _proxy("POST", "/tts", timeout=60.0, json_body=body.model_dump())


# --- session log -------------------------------------------------------------


class LogIn(BaseModel):
    session: str = Field(..., min_length=1, max_length=200)
    who: str = Field(..., min_length=1, max_length=50)
    text: str = Field(..., min_length=1, max_length=8000)


@router.post("/log")
async def log_turn(body: LogIn, _: User = CurrentUser):
    return await _proxy("POST", "/log", json_body=body.model_dump())


@router.get("/latest")
async def latest(_: User = CurrentUser):
    return await _proxy("GET", "/latest")


@router.get("/history")
async def history(_: User = CurrentUser):
    return await _proxy("GET", "/history")


# --- status ------------------------------------------------------------------


@router.get("/status")
async def status(_: User = CurrentUser):
    url, key = _config()
    if not url or not key:
        return {"configured": False}
    try:
        async with _client(10.0) as client:
            resp = await client.get("/latest")
        return {"configured": True, "upstream_status": resp.status_code}
    except httpx.HTTPError as exc:
        return {"configured": True, "upstream_status": None, "error": str(exc)}


__all__ = ["router"]

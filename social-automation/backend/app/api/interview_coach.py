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

Also proxied: ``/stt`` WebSocket (browser passes the SocialAuto JWT as
``?token=``; the worker key goes upstream as ``x-key``).

Not proxied: arbitrary ``/engines/*`` paths (only chat/completions is used).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid

import httpx
import websockets
from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_user_role, require_admin
from app.core.config import get_settings
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)
router = APIRouter()

# The worker holds a single-tenant interview_db (owner CV + session log),
# so the whole surface is admin-gated — same role floor as other
# operator-only routers.
CurrentUser = Depends(require_admin)


def _config() -> tuple[str, str]:
    s = get_settings()
    url = (getattr(s, "INTERVIEW_COACH_URL", "") or "").strip().rstrip("/")
    key = (getattr(s, "INTERVIEW_COACH_KEY", "") or "").strip()
    return url, key


def _client(timeout: float) -> httpx.AsyncClient:
    url, key = _config()
    if not url or not key:
        raise HTTPException(503, "interview-coach not configured (INTERVIEW_COACH_URL/INTERVIEW_COACH_KEY)")
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
            resp = await client.request(method, path, content=content, json=json_body, headers=headers)
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
        "POST",
        "/transcribe",
        timeout=60.0,
        content=audio,
        content_type="application/octet-stream",
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


def _raw_client(timeout: float) -> httpx.AsyncClient:
    url, key = _config()
    if not url or not key:
        raise HTTPException(503, "interview-coach not configured (INTERVIEW_COACH_URL/INTERVIEW_COACH_KEY)")
    return httpx.AsyncClient(base_url=url, headers={"x-key": key}, timeout=timeout)


async def _proxy_stream(
    method: str,
    path: str,
    *,
    timeout: float = 30.0,
    content: bytes | None = None,
    json_body: dict | None = None,
    headers: dict | None = None,
) -> Response:
    """Stream an upstream response body chunk-by-chunk (SSE etc.).

    The httpx client + response stay open until the iterator finishes or the
    client disconnects (generator ``finally`` closes both).
    """
    client = _raw_client(timeout)
    try:
        req = client.build_request(method, path, content=content, json=json_body, headers=headers or {})
        resp = await client.send(req, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        logger.warning("interview-coach %s %s failed: %s", method, path, exc)
        raise HTTPException(502, "interview-coach upstream error") from exc

    if resp.status_code >= 400:
        err_body = await resp.aread()
        await resp.aclose()
        await client.aclose()
        try:
            payload = json.loads(err_body)
        except ValueError:
            payload = {"raw": err_body[:2000].decode("utf-8", "replace")}
        return Response(
            content=json.dumps(payload),
            status_code=resp.status_code,
            media_type="application/json",
        )

    media = resp.headers.get("content-type", "application/json")

    async def _body():
        try:
            async for chunk in resp.aiter_raw():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    return StreamingResponse(_body(), status_code=resp.status_code, media_type=media.split(";")[0])


@router.post("/chat")
async def chat(body: ChatIn, _: User = CurrentUser):
    payload = {k: v for k, v in body.model_dump().items() if v is not None}
    if body.stream:
        return await _proxy_stream("POST", "/engines/chat/completions", timeout=90.0, json_body=payload)
    return await _proxy("POST", "/engines/chat/completions", timeout=90.0, json_body=payload)


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
        logger.warning("interview-coach status probe failed: %r", exc)
        return {"configured": True, "upstream_status": None, "error": "upstream unreachable"}


__all__ = ["router"]


# --- live STT websocket -------------------------------------------------------
#
# The worker's /stt lane is a WebSocket into its VoiceAgent Durable Object
# (Deepgram Flux streaming STT). Browsers can't set headers on WS upgrades,
# so clients pass the SocialAuto JWT as ?token=; the worker AUTH_SECRET goes
# upstream as x-key on the server-side connect.


async def _ws_authenticate(token: str | None, db: AsyncSession) -> User:
    payload = decode_token(token) if token else None
    if not payload or payload.get("type") != "access":
        raise HTTPException(401, "Invalid token")
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(401, "Invalid token")
    try:
        uid = uuid.UUID(str(user_id))
    except ValueError:
        raise HTTPException(401, "Invalid token") from None
    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(401, "User not found")
    role = await get_user_role(user, db)
    if role not in (UserRole.ADMIN, UserRole.OWNER):
        raise HTTPException(403, "Requires admin role or higher")
    return user


def _ws_upstream_url(ch: str) -> str:
    url, _ = _config()
    ws = url.replace("https://", "wss://").replace("http://", "ws://")
    return f"{ws}/stt?ch={ch}"


async def _pump_stt(client: WebSocket, upstream) -> None:
    async def c2u() -> None:
        while True:
            msg = await client.receive()
            if msg["type"] == "websocket.disconnect":
                return
            data = msg.get("bytes") or msg.get("text")
            if data is None:
                continue
            await upstream.send(data)

    async def u2c() -> None:
        async for data in upstream:
            if isinstance(data, bytes):
                await client.send_bytes(data)
            else:
                await client.send_text(data)

    tasks = [asyncio.create_task(c2u()), asyncio.create_task(u2c())]
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for t in pending:
        t.cancel()
    for t in done:
        exc = t.exception()
        if exc is not None:
            logger.debug("stt pump ended: %r", exc)


@router.websocket("/stt")
async def stt_ws(websocket: WebSocket, db: AsyncSession = Depends(get_db)):
    token = websocket.query_params.get("token")
    ch_raw = websocket.query_params.get("ch") or "tab"
    ch = "".join(c for c in ch_raw if c.isalnum() or c in "-_")[:64] or "tab"
    try:
        await _ws_authenticate(token, db)
    except HTTPException:
        await websocket.close(code=4401)
        return
    url, key = _config()
    if not url or not key:
        await websocket.close(code=4503)
        return
    try:
        async with websockets.connect(
            _ws_upstream_url(ch),
            additional_headers={"x-key": key},
            max_size=None,
        ) as upstream:
            await websocket.accept()
            await _pump_stt(websocket, upstream)
    except Exception as exc:  # upstream connect/pump failure
        logger.warning("interview-coach stt upstream failed: %r", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

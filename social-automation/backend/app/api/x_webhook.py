"""X Activity API webhook — CRC challenge + event ingest.

X sends a GET with ?crc_token= to verify the endpoint; we respond with
``{"response_token": "sha256=<hmac>"}`` signed with the consumer secret.
POST carries the event payload (DMs, mentions, follows).
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from base64 import b64encode
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/twitter", tags=["webhooks"])


def _consumer_secret() -> str:
    return os.environ.get("TWITTER_API_SECRET", "")


@router.get("")
async def crc_challenge(crc_token: str = ""):
    """X CRC challenge — respond with HMAC-SHA256 of the token."""
    if not crc_token:
        return PlainTextResponse("missing crc_token", status_code=400)
    secret = _consumer_secret()
    if not secret:
        return PlainTextResponse("consumer secret not configured", status_code=500)
    digest = hmac.new(secret.encode(), crc_token.encode(), hashlib.sha256).digest()
    return {"response_token": "sha256=" + b64encode(digest).decode()}


@router.post("")
async def event_ingest(request: Request) -> JSONResponse:
    """X Activity API events — route to the messenger/digest pipeline."""
    try:
        payload: dict[str, Any] = await request.json()
    except Exception:
        return JSONResponse({"ok": False, "reason": "bad json"}, status_code=400)

    # DM events, tweet mentions, follows — log + hand to the pipeline
    events = {
        "dm_events": payload.get("direct_message_events", []),
        "tweet_events": payload.get("tweet_create_events", []),
        "follow_events": payload.get("follow_events", []),
    }
    for kind, evs in events.items():
        if evs:
            logger.info("X webhook %s: %d event(s)", kind, len(evs))
    return JSONResponse({"ok": True})

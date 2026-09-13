"""Messenger webhook sidecar — async event processing.

A lightweight HTTP service that receives Messenger webhook events from
the main social-api webhook endpoint and processes them asynchronously.
This keeps the main API responsive (Meta requires 200 within 5 seconds)
while allowing heavier work like AI auto-reply, conversation persistence,
and analytics.

The sidecar runs as a Docker Compose service on port 9230 (not 9229 —
airbyte-mcp health) and is called by the main API's webhook handler via
internal HTTP.

Endpoints:
    POST /process  — Process a webhook event (called by social-api)
    GET  /health    — Health check
    GET  /stats     — Processing statistics
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from collections import deque

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("messenger-sidecar")

app = FastAPI(title="Messenger Webhook Sidecar", version="1.0.0")

# Configuration
SOCIAL_API_URL = os.getenv("SOCIAL_API_URL", "http://social-api:8083")
ADMIN_EMAIL = os.getenv("SOCIAL_ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.getenv("SOCIAL_ADMIN_PASSWORD", "")
CLOUDFLARE_API_TOKEN = os.getenv("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_ACCOUNT_ID = os.getenv("CLOUDFLARE_ACCOUNT_ID", "")
DMR_BASE_URL = os.getenv("DMR_BASE_URL", "http://host.docker.internal:12434")
DEFAULT_MODEL = os.getenv("MESSENGER_AI_MODEL", "@cf/meta/llama-3.1-8b-instruct")

# Stats
_stats = {
    "events_received": 0,
    "events_processed": 0,
    "auto_replies_sent": 0,
    "errors": 0,
}
_recent_events: deque = deque(maxlen=100)
_seen_message_ids: deque = deque(maxlen=10000)  # idempotency

# Facebook object ids are numeric (optional underscore compound ids).
_FB_ID_RE = re.compile(r"^[0-9]{1,64}(_[0-9]{1,64})?$")
_GRAPH_MESSAGES_TMPL = "https://graph.facebook.com/v25.0/{page_id}/messages"


def _sanitize_log_text(text: str, max_len: int = 200) -> str:
    """Strip newlines/control chars before logging untrusted identifiers."""
    cleaned = (text or "").replace("\n", "\\n").replace("\r", "\\r")
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", cleaned)
    return cleaned[:max_len]


def _validate_fb_id(value: str, label: str = "id") -> str:
    """Validate Graph path ids to prevent partial SSRF via crafted identifiers."""
    value = (value or "").strip()
    if not value:
        raise ValueError(f"Facebook {label} is empty")
    if not _FB_ID_RE.match(value):
        raise ValueError(f"Invalid Facebook {label} format")
    return value


def _graph_messages_url(page_id: str) -> str:
    safe_page_id = _validate_fb_id(page_id, "page_id")
    return _GRAPH_MESSAGES_TMPL.format(page_id=safe_page_id)


class WebhookEvent(BaseModel):
    page_id: str
    sender_psid: str
    recipient_id: str
    message_text: str = ""
    message_type: str = ""  # text, postback, delivery, read, echo
    postback_payload: str = ""
    message_mid: str = ""
    timestamp: int = 0


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "messenger-sidecar", "version": "1.0.0"}


@app.get("/stats")
async def stats() -> dict:
    return {
        **_stats,
        "recent_events": len(_recent_events),
        "dedup_cache_size": len(_seen_message_ids),
    }


@app.post("/process")
async def process_event(event: WebhookEvent) -> JSONResponse:
    """Process a single webhook event."""
    _stats["events_received"] += 1

    # Idempotency: skip duplicate message IDs
    if event.message_mid and event.message_mid in _seen_message_ids:
        logger.info("Skipping duplicate message_mid=%s", _sanitize_log_text(event.message_mid))
        return JSONResponse({"status": "duplicate", "message_id": event.message_mid})
    if event.message_mid:
        _seen_message_ids.append(event.message_mid)

    _recent_events.append({
        "page_id": event.page_id,
        "sender_psid": event.sender_psid,
        "type": event.message_type,
        "text": event.message_text[:100],
        "payload": event.postback_payload[:100],
        "ts": time.time(),
    })

    # Only process text and postback messages for auto-reply
    if event.message_type not in ("text", "postback") or not event.sender_psid:
        _stats["events_processed"] += 1
        return JSONResponse({"status": "ok", "action": "ignored", "type": event.message_type})

    # Process asynchronously — don't block the response
    asyncio.create_task(_handle_message(event))

    _stats["events_processed"] += 1
    return JSONResponse({"status": "ok", "action": "processing", "type": event.message_type})


async def _handle_message(event: WebhookEvent) -> None:
    """Handle a message event: look up account, check auto-reply, generate AI response."""
    try:
        # Find the Facebook Page account and check auto-reply config
        account_info = await _get_account_and_config(event.page_id)
        if not account_info:
            logger.warning("No Facebook Page account found for page_id=%s", _sanitize_log_text(event.page_id))
            return

        account_id, page_token, auto_reply_config, page_name = account_info

        if not auto_reply_config.get("enabled", False):
            logger.debug("Auto-reply disabled for page_id=%s", _sanitize_log_text(event.page_id))
            return

        # Send typing indicator
        await _send_sender_action(page_token, event.page_id, event.sender_psid, "typing_on")

        # Generate AI response
        reply_text = await _generate_ai_response(
            auto_reply_config, event.message_text, page_name
        )

        # Send the reply
        await _send_text_message(page_token, event.page_id, event.sender_psid, reply_text)

        # Stop typing indicator
        await _send_sender_action(page_token, event.page_id, event.sender_psid, "typing_off")

        _stats["auto_replies_sent"] += 1
        logger.info(
            "Auto-reply sent to psid=%s for page_id=%s",
            _sanitize_log_text(event.sender_psid),
            _sanitize_log_text(event.page_id),
        )

    except Exception as exc:
        _stats["errors"] += 1
        logger.error("Failed to process event: %s", exc, exc_info=True)


async def _get_account_and_config(page_id: str) -> tuple[str, str, dict, str] | None:
    """Get account ID, page token, auto-reply config, and page name from social-api."""
    token = await _get_admin_token()
    if not token:
        return None

    async with httpx.AsyncClient(timeout=30) as client:
        # Find the Facebook Page account
        resp = await client.get(
            f"{SOCIAL_API_URL}/api/v1/accounts",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code == 401:
            # Token expired — clear cache and retry once
            global _api_token
            _api_token = ""
            token = await _get_admin_token()
            if not token:
                return None
            resp = await client.get(
                f"{SOCIAL_API_URL}/api/v1/accounts",
                headers={"Authorization": f"Bearer {token}"},
            )
        if resp.status_code != 200:
            logger.error("Failed to list accounts: %s", resp.status_code)
            return None

        accounts = resp.json()
        if isinstance(accounts, dict):
            accounts = accounts.get("data", accounts.get("accounts", []))

        for acct in accounts:
            if acct.get("platform") == "facebook" and acct.get("account_id") == page_id:
                meta = acct.get("meta_data", {})
                page_token = meta.get("page_token", "")
                auto_reply = meta.get("messenger_auto_reply", {})
                page_name = acct.get("display_name", "")
                return str(acct.get("id", "")), page_token, auto_reply, page_name

    return None


_api_token: str = ""


async def _get_admin_token() -> str:
    """Get admin token from social-api (re-authenticates on expiry)."""
    global _api_token
    if _api_token:
        return _api_token
    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        return ""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{SOCIAL_API_URL}/api/v1/auth/login",
            data={"username": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if resp.status_code == 200:
            _api_token = resp.json().get("access_token", "")
    return _api_token


async def _send_sender_action(page_token: str, page_id: str, psid: str, action: str) -> None:
    """Send a sender action (typing_on, typing_off, mark_seen)."""
    url = _graph_messages_url(page_id)
    safe_psid = _validate_fb_id(psid, "psid")
    async with httpx.AsyncClient(timeout=10) as client:
        await client.post(
            url,
            params={"access_token": page_token},
            json={
                "recipient": {"id": safe_psid},
                "sender_action": action,
            },
        )


async def _send_text_message(page_token: str, page_id: str, psid: str, text: str) -> None:
    """Send a text message via the Send API."""
    url = _graph_messages_url(page_id)
    safe_psid = _validate_fb_id(psid, "psid")
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            url,
            params={"access_token": page_token},
            json={
                "recipient": {"id": safe_psid},
                "messaging_type": "RESPONSE",
                "message": {"text": text},
            },
        )
        if resp.status_code != 200:
            logger.error(
                "Send API error: %s %s",
                resp.status_code,
                _sanitize_log_text(resp.text[:200]),
            )


async def _generate_ai_response(config: dict, user_message: str, page_name: str) -> str:
    """Generate an AI response using Cloudflare Workers AI (free) with DMR fallback."""
    system_prompt = config.get(
        "system_prompt", "You are a helpful assistant. Reply concisely and professionally."
    ).replace("{page_name}", page_name or "us")
    model = config.get("model", DEFAULT_MODEL)
    max_tokens = config.get("max_tokens", 200)
    fallback = config.get("fallback_text", "Thanks for your message! We'll get back to you soon.")

    # Try Cloudflare Workers AI first (free tier)
    if CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID:
        try:
            url = f"https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai/run/{model}"
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {CLOUDFLARE_API_TOKEN}"},
                    json={
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_message},
                        ],
                        "max_tokens": max_tokens,
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if data.get("result") and data["result"].get("response"):
                        return data["result"]["response"].strip()
        except Exception as exc:
            logger.warning("Cloudflare AI failed: %s", exc)

    # Try DMR (local Docker Model Runner) as fallback
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{DMR_BASE_URL}/engines/v1/chat/completions",
                json={
                    "model": "ai/qwen3:8b-q4_K_M",
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                    "max_tokens": max_tokens,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", fallback).strip()
    except Exception as exc:
        logger.warning("DMR AI failed: %s", exc)

    return fallback


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=9230)

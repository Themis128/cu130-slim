"""Telegram gate alerts via the telegram-mcp HTTP server (host :8765).

Sends through the user's own Telethon session — no bot token needed.
Target: Saved Messages ("me") or TELEGRAM_GATE_CHAT_ID.
"""
import logging
import os

import httpx

logger = logging.getLogger(__name__)

BASE = os.environ.get("TELEGRAM_MCP_URL", "http://host.docker.internal:8765/mcp")


async def _rpc(client, method, params=None, sid=None):
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if sid:
        headers["Mcp-Session-Id"] = sid
    r = await client.post(BASE, headers=headers, json={
        "jsonrpc": "2.0", "id": 1, "method": method,
        "params": params or {}})
    return r


async def send_telegram(text: str, chat: str = "me") -> bool:
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            init = await _rpc(c, "initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "socialauto", "version": "1"}})
            sid = init.headers.get("mcp-session-id")
            await _rpc(c, "notifications/initialized", sid=sid)
            await _rpc(c, "tools/call", {
                "name": "send_message",
                "arguments": {"chat_id": chat, "message": text}}, sid=sid)
        return True
    except Exception as e:  # noqa: BLE001 — alerting never breaks callers
        logger.warning("telegram send failed: %s", e)
        return False

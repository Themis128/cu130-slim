"""Remaining expiry-handling arms of the Meta token refresh worker."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

import app.worker.tasks.instagram_token_refresh as I
from tests.unit.test_instagram_token_refresh import (  # noqa: F401 - _common is autouse
    _acct,
    _common,
    _resp,
    _wire,
)


@pytest.mark.asyncio
async def test_debug_token_without_expiry_falls_through_to_refresh(monkeypatch):
    acct = _acct(platform="facebook", access_token_enc="enc:EAAfb", meta_data={})
    _, http = _wire(monkeypatch, [acct], routes={
        "/debug_token": _resp(400, {"error": {"message": "bad app secret"}}),
        "/oauth/access_token": _resp(200, {"access_token": "NEWFB"}),
    })
    out = await I._refresh_instagram_tokens_async()
    assert out["tokens_refreshed"] == 1
    assert "token_never_expires" not in acct.meta_data
    assert acct.access_token_enc == "enc:NEWFB"
    assert any("/debug_token" in u for u in http.calls)


@pytest.mark.asyncio
async def test_expiry_alert_not_repeated_once_sent(monkeypatch):
    acct = _acct(token_expires_at=datetime.now(UTC) + timedelta(days=2),
                 meta_data={"expiry_alert_sent": True})
    _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(200, {"access_token": "NEW"})})
    out = await I._refresh_instagram_tokens_async()
    I.post_alert_to_slack.assert_not_awaited()
    assert acct.meta_data["expiry_alert_sent"] is True  # kept: still ≤7d
    assert out["tokens_refreshed"] == 1


@pytest.mark.asyncio
async def test_already_expired_token_keeps_alert_flag(monkeypatch):
    acct = _acct(token_expires_at=datetime.now(UTC) - timedelta(days=3),
                 meta_data={"expiry_alert_sent": True})
    _wire(monkeypatch, [acct], routes={
        "/refresh_access_token": _resp(200, {"access_token": "NEW"})})
    await I._refresh_instagram_tokens_async()
    I.post_alert_to_slack.assert_not_awaited()
    assert acct.meta_data.get("expiry_alert_sent") is True

"""Unit tests for WhatsApp + Telegram router security/dispatch helpers.

The webhook signature verification and credential-decrypt paths are
security-critical and were uncovered.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api import telegram, whatsapp
from app.api.telegram import (
    _decrypt_bot_token,
    _new_webhook_secret,
    _sanitize,
    _token_bytes,
    _webhook_base,
    _webhook_url_for,
)
from app.api.whatsapp import (
    _get_verify_token,
    _get_whatsapp_client,
    _verify_webhook_signature,
    verify_webhook,
)
from app.core.config import settings

# ── WhatsApp webhook signature ───────────────────────────────────────


def _sig(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_signature_valid_accepted():
    body = b'{"entry":[]}'
    assert _verify_webhook_signature(body, _sig(body, "s3cret"), "s3cret") is True


def test_webhook_signature_tampered_body_rejected():
    sig = _sig(b'{"entry":[]}', "s3cret")
    assert _verify_webhook_signature(b'{"entry":[1]}', sig, "s3cret") is False


def test_webhook_signature_wrong_secret_rejected():
    body = b'{"entry":[]}'
    assert _verify_webhook_signature(body, _sig(body, "other"), "s3cret") is False


def test_webhook_signature_malformed_headers_rejected():
    body = b"{}"
    assert _verify_webhook_signature(body, "", "s3cret") is False
    assert _verify_webhook_signature(body, "sha1=abc", "s3cret") is False
    assert _verify_webhook_signature(body, "sha256=", "s3cret") is False


# ── WhatsApp verify_webhook (hub challenge) ──────────────────────────


@pytest.mark.asyncio
async def test_verify_webhook_echoes_challenge(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "tok123", raising=False)
    resp = await verify_webhook(hub_mode="subscribe", hub_verify_token="tok123", hub_challenge="CH-77")
    assert resp.status_code == 200 and resp.body == b"CH-77"


@pytest.mark.asyncio
async def test_verify_webhook_wrong_token_403(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "tok123", raising=False)
    with pytest.raises(HTTPException) as e:
        await verify_webhook(hub_mode="subscribe", hub_verify_token="bad", hub_challenge="x")
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_verify_webhook_wrong_mode_403(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "tok123", raising=False)
    with pytest.raises(HTTPException):
        await verify_webhook(hub_mode="unsubscribe", hub_verify_token="tok123", hub_challenge="x")


def test_get_verify_token_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_VERIFY_TOKEN", "", raising=False)
    assert _get_verify_token() == "cloudless_whatsapp_verify"


# ── WhatsApp client construction ─────────────────────────────────────


def _wa_account(meta):
    return SimpleNamespace(
        id=uuid.uuid4(), team_id=uuid.uuid4(), platform="whatsapp",
        account_type="business", account_id="waba1", meta_data=meta,
    )


def test_whatsapp_client_400_missing_credentials():
    with pytest.raises(HTTPException) as e:
        _get_whatsapp_client(_wa_account({}))
    assert e.value.status_code == 400
    with pytest.raises(HTTPException):
        _get_whatsapp_client(_wa_account({"access_token": "EAAtok"}))  # no phone_number_id


def test_whatsapp_client_plaintext_token(monkeypatch):
    client = _get_whatsapp_client(_wa_account({
        "access_token": "EAAplain", "phone_number_id": "123", "display_phone_number": "+301",
    }))
    assert client.phone_number_id == "123" and client.business_phone == "+301"


def test_whatsapp_client_encrypted_token_decrypted(monkeypatch):
    seen = {}

    def _dec(raw):
        seen["raw"] = raw
        return "EAAdec"

    monkeypatch.setattr(whatsapp, "decrypt_token", _dec)
    client = _get_whatsapp_client(_wa_account({
        "access_token": "enc-blob", "phone_number_id": "123",
    }))
    assert seen["raw"] == "enc-blob" and client.phone_number_id == "123"


# ── Telegram helpers ─────────────────────────────────────────────────


def test_sanitize_escapes_and_truncates():
    assert _sanitize("a\nb\rc") == "a\\nb\\rc"
    assert _sanitize("x" * 500) == "x" * 400
    assert _sanitize("") == ""


def test_token_bytes_passthrough_and_encode():
    assert _token_bytes(b"abc") == b"abc"
    assert _token_bytes("abc") == b"abc"


def _tg_account(meta=None, enc=None):
    return SimpleNamespace(
        id=uuid.uuid4(), platform="telegram", account_id="bot1",
        meta_data=meta or {}, access_token_enc=enc,
    )


def test_decrypt_bot_token_from_meta(monkeypatch):
    monkeypatch.setattr(telegram, "decrypt_token", lambda raw: "dec:" + raw.decode())
    assert _decrypt_bot_token(_tg_account(meta={"bot_token_enc": "blob"})) == "dec:blob"


def test_decrypt_bot_token_falls_back_to_account_field(monkeypatch):
    def _dec(raw):
        if raw == b"meta-blob":
            raise ValueError("corrupt")
        return "fallback-token"

    monkeypatch.setattr(telegram, "decrypt_token", _dec)
    acc = _tg_account(meta={"bot_token_enc": "meta-blob"}, enc="acct-enc")
    assert _decrypt_bot_token(acc) == "fallback-token"


def test_decrypt_bot_token_400_when_nothing_works(monkeypatch):
    monkeypatch.setattr(telegram, "decrypt_token", lambda raw: (_ for _ in ()).throw(ValueError()))
    with pytest.raises(HTTPException) as e:
        _decrypt_bot_token(_tg_account())
    assert e.value.status_code == 400


def test_webhook_base_prefers_explicit_setting(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_BASE", "https://hook.example.com/api/v1/", raising=False)
    assert _webhook_base() == "https://hook.example.com/api/v1"


def test_webhook_base_strips_media_url_path(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_BASE", "", raising=False)
    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "https://social.cloudless.gr/media", raising=False)
    assert _webhook_base() == "https://social.cloudless.gr/api/v1"


def test_webhook_base_default(monkeypatch):
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_BASE", "", raising=False)
    monkeypatch.setattr(settings, "MEDIA_PUBLIC_BASE_URL", "", raising=False)
    assert _webhook_base() == "https://social.cloudless.gr/api/v1"


def test_webhook_url_for_appends_account_id(monkeypatch):
    aid = uuid.uuid4()
    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_BASE", "https://h.example.com/api/v1", raising=False)
    assert _webhook_url_for(aid) == f"https://h.example.com/api/v1/telegram/webhook/{aid}"


def test_new_webhook_secret_charset():
    import re
    for _ in range(20):
        s = _new_webhook_secret()
        assert re.fullmatch(r"[A-Za-z0-9_-]+", s) and len(s) <= 64

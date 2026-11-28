"""Coverage for messenger_sidecar CF token resolution + degraded-reply arms.

Complements test_messenger_sidecar.py: exercises the alternate-name token
lookup in ``_cf_token()`` (2026-11-28 incident class), the "token present but
no account id" skip arm, and the empty-fallback path in
``_generate_ai_response``.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import app.services.messenger_sidecar as M

CF_KEYS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_AI_API_TOKEN", "CLOUDFLARE_WORKERS_AI_TOKEN")


def _clean_env(**extra: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in CF_KEYS}
    env.update(extra)
    return env


def _resp(status=200, json_body=None, text=""):
    return SimpleNamespace(status_code=status, json=lambda: json_body or {}, text=text)


class _Http:
    def __init__(self, post):
        self._post = post

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kw):
        return await self._post(url, **kw)


def _patch_http(monkeypatch, post):
    monkeypatch.setattr(M.httpx, "AsyncClient", lambda *a, **k: _Http(post))


async def _dmr_down(url, **kw):
    raise ConnectionError("dmr down")


# ── _cf_token resolution chain ───────────────────────────────────────


class TestCfToken:
    def test_module_constant_wins(self, monkeypatch):
        monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "  canon  ")
        with patch.dict(os.environ, _clean_env(CLOUDFLARE_WORKERS_AI_TOKEN="w"), clear=True):
            assert M._cf_token() == "canon"

    def test_ai_api_token_name(self, monkeypatch):
        monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "")
        with patch.dict(os.environ, _clean_env(CLOUDFLARE_AI_API_TOKEN=" ai-tok "), clear=True):
            assert M._cf_token() == "ai-tok"

    def test_workers_ai_token_name(self, monkeypatch):
        monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "   ")
        with patch.dict(os.environ, _clean_env(CLOUDFLARE_WORKERS_AI_TOKEN="workers-tok"), clear=True):
            assert M._cf_token() == "workers-tok"

    def test_blank_alternates_skipped(self, monkeypatch):
        monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "")
        env = _clean_env(CLOUDFLARE_AI_API_TOKEN="  ", CLOUDFLARE_WORKERS_AI_TOKEN="")
        with patch.dict(os.environ, env, clear=True):
            assert M._cf_token() == ""


# ── _generate_ai_response skip / fallback arms ───────────────────────


@pytest.mark.asyncio
async def test_cf_skipped_when_account_id_missing(monkeypatch, caplog):
    monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(M, "CLOUDFLARE_ACCOUNT_ID", "")
    post = AsyncMock(side_effect=_dmr_down)
    _patch_http(monkeypatch, post)
    with patch.dict(os.environ, _clean_env(CLOUDFLARE_WORKERS_AI_TOKEN="workers-tok"), clear=True):
        with caplog.at_level("WARNING", logger="messenger-sidecar"):
            out = await M._generate_ai_response({"fallback_text": "FB"}, "hi", "P")
    assert out == "FB"
    assert "CLOUDFLARE_ACCOUNT_ID not configured" in caplog.text
    # only the DMR attempt — CF was never called without an account id
    assert post.await_count == 1


@pytest.mark.asyncio
async def test_cf_used_via_alternate_token_name(monkeypatch):
    monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(M, "CLOUDFLARE_ACCOUNT_ID", "acct")
    seen = {}

    async def _post(url, **kw):
        if "chat/completions" in url:
            raise ConnectionError("dmr down")
        seen["auth"] = kw["headers"]["Authorization"]
        return _resp(200, {"result": {"response": " cf reply "}})

    _patch_http(monkeypatch, _post)
    with patch.dict(os.environ, _clean_env(CLOUDFLARE_AI_API_TOKEN="ai-tok"), clear=True):
        out = await M._generate_ai_response({}, "hi", "P")
    assert out == "cf reply"
    assert seen["auth"] == "Bearer ai-tok"


@pytest.mark.asyncio
async def test_cf_token_rejected_logs_rotate_hint(monkeypatch, caplog):
    monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "dead")
    monkeypatch.setattr(M, "CLOUDFLARE_ACCOUNT_ID", "acct")

    async def _post(url, **kw):
        if "chat/completions" in url:
            raise ConnectionError("dmr down")
        return _resp(401, text="unauthorized")

    _patch_http(monkeypatch, _post)
    with caplog.at_level("WARNING", logger="messenger-sidecar"):
        out = await M._generate_ai_response({"fallback_text": "FB"}, "hi", "P")
    assert out == "FB"
    assert "token rejected" in caplog.text


@pytest.mark.asyncio
async def test_empty_fallback_text_returned_without_degraded_log(monkeypatch, caplog):
    monkeypatch.setattr(M, "CLOUDFLARE_API_TOKEN", "")
    monkeypatch.setattr(M, "CLOUDFLARE_ACCOUNT_ID", "")
    _patch_http(monkeypatch, _dmr_down)
    with patch.dict(os.environ, _clean_env(), clear=True):
        with caplog.at_level("INFO", logger="messenger-sidecar"):
            out = await M._generate_ai_response({"fallback_text": ""}, "hi", "P")
    assert out == ""
    assert "no token under" in caplog.text
    assert "degraded to static fallback" not in caplog.text

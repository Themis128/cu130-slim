"""Tests for the cold-load timeout in _call_dmr_chat_internal.

A model that isn't resident pays the full GGUF->VRAM load (multi-minute for
the 8B on WSL2). The default 30s non-schema timeout can never span that, so
the request must get an extended cold-load timeout; resident models keep the
caller's tight timeout so a wedged runner still fails fast. Timeout errors
must surface the exception type (httpx.TimeoutException stringifies empty).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.services import dmr

MODEL = "ai/smollm3"


class _Resp:
    def __init__(self, status_code: int = 200, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


class _Client:
    """Fake httpx.AsyncClient that records per-request timeouts."""

    is_closed = False

    def __init__(self, *, fail: bool = False):
        self.timeouts: list[float] = []
        self.fail = fail

    async def post(self, url, json=None, timeout=None, **_kw):
        self.timeouts.append(timeout)
        if self.fail:
            raise httpx.TimeoutException("")
        return _Resp(200, {"choices": [{"message": {"content": "ok"}}]})

    async def get(self, url, **_kw):
        return _Resp(404, {})


class _Slot:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _patch_common(monkeypatch, running: set[str]) -> _Client:
    from app.services import gpu_arbiter

    monkeypatch.setattr(gpu_arbiter, "await_media_idle", AsyncMock())
    monkeypatch.setattr(gpu_arbiter, "dmr_slot", lambda: _Slot())
    monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(dmr, "_has_vram_for_model", AsyncMock(return_value=True))
    monkeypatch.setattr(dmr, "_ensure_model_configured", AsyncMock())
    monkeypatch.setattr(dmr, "_running_dmr_models", AsyncMock(return_value=running))
    return _Client()


@pytest.fixture(autouse=True)
def _clean_state():
    dmr._state.configured = {}
    dmr._state.vram = {}
    dmr._state.vram_check = 0.0
    yield
    dmr._state.configured = {}
    dmr._state.vram = {}
    dmr._state.vram_check = 0.0


class TestColdLoadTimeout:
    @pytest.mark.asyncio
    async def test_cold_model_gets_extended_timeout(self, monkeypatch):
        client = _patch_common(monkeypatch, running=set())
        monkeypatch.setattr(dmr, "_get_client", AsyncMock(return_value=client))
        result = await dmr.call_dmr_chat("hi", model_override=MODEL, max_tokens=5)
        assert result["text"] == "ok"
        assert client.timeouts
        assert all(t == dmr.settings.DMR_COLD_TIMEOUT for t in client.timeouts)

    @pytest.mark.asyncio
    async def test_resident_model_keeps_caller_timeout(self, monkeypatch):
        client = _patch_common(monkeypatch, running={MODEL.lower()})
        monkeypatch.setattr(dmr, "_get_client", AsyncMock(return_value=client))
        result = await dmr.call_dmr_chat("hi", model_override=MODEL, max_tokens=5)
        assert result["text"] == "ok"
        # call_dmr_chat's non-schema timeout is 30s — must not be stretched
        assert all(t == 30.0 for t in client.timeouts)

    @pytest.mark.asyncio
    async def test_cold_timeout_respects_caller_floor(self, monkeypatch):
        client = _patch_common(monkeypatch, running=set())
        monkeypatch.setattr(dmr, "_get_client", AsyncMock(return_value=client))
        await dmr.call_dmr_chat(
            "hi", model_override=MODEL, schema={"type": "object"}
        )
        # schema calls default to 180s; cold extends them to DMR_COLD_TIMEOUT
        assert all(t == dmr.settings.DMR_COLD_TIMEOUT for t in client.timeouts)


class TestRetryErrorMessage:
    @pytest.mark.asyncio
    async def test_timeout_error_names_exception_type(self, monkeypatch):
        client = _patch_common(monkeypatch, running={MODEL.lower()})
        client.fail = True
        monkeypatch.setattr(dmr, "_get_client", AsyncMock(return_value=client))
        monkeypatch.setattr(dmr, "_free_gpu_memory", AsyncMock())
        monkeypatch.setattr("asyncio.sleep", AsyncMock())
        with pytest.raises(ConnectionError) as excinfo:
            await dmr.call_dmr_chat("hi", model_override=MODEL, max_tokens=5)
        msg = str(excinfo.value)
        assert "TimeoutException" in msg
        assert not msg.rstrip().endswith(":")

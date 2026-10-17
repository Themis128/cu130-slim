"""Remaining reachable branch arms in app/services/dmr.py.

Covers:
- shared-client re-creation when the old client was closed on the same
  loop, including losing the race to another coroutine
- nvidia-smi with short output; benchmark with a failing CLI or an
  odd-shaped payload
- override-store values that aren't objects
- a speculative configure that DMR rejects
- warmup when the text model is already the primary
- a configure payload with no keep_alive or runtime flags
- the chat VRAM gate recovering at each step, or failing with no tiny
  fallback
- stream keep-alive lines and empty deltas
- embedding and vision VRAM recovering after one heal
- a fenced JSON block with no closing fence

Redis, the DMR HTTP API, nvidia-smi and the GPU arbiter are all faked."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import app.services.dmr as D
from tests.unit.test_dmr_coverage import _chat_env, _reset_state, _resp, _vision_env  # noqa: F401

# ── shared client lifecycle ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_client_replaces_closed_client_on_same_loop():
    loop = asyncio.get_running_loop()
    old = httpx.AsyncClient()
    await old.aclose()
    lock = asyncio.Lock()
    D._state.client, D._state.client_loop, D._state.client_lock = old, loop, lock
    new = await D._get_client()
    try:
        assert new is not old and not new.is_closed
        assert D._state.client_lock is lock  # same loop → lock kept
    finally:
        await new.aclose()


@pytest.mark.asyncio
async def test_get_client_waiter_reuses_client_built_while_it_waited():
    loop = asyncio.get_running_loop()
    old = httpx.AsyncClient()
    await old.aclose()
    lock = asyncio.Lock()
    D._state.client, D._state.client_loop, D._state.client_lock = old, loop, lock
    await lock.acquire()
    waiter = asyncio.create_task(D._get_client())
    await asyncio.sleep(0)  # waiter now blocked on the lock
    fresh = httpx.AsyncClient()
    D._state.client = fresh  # another coroutine finished the rebuild
    lock.release()
    try:
        assert await waiter is fresh
    finally:
        await fresh.aclose()


# ── probes ───────────────────────────────────────────────────────────


def test_nvidia_smi_short_output_is_unmeasurable(monkeypatch):
    monkeypatch.setattr(D.subprocess, "run", Mock(return_value=SimpleNamespace(returncode=0, stdout="1024, 2048\n")))
    assert D._nvidia_smi_vram() is None


@pytest.mark.asyncio
async def test_benchmark_cli_failure_and_empty_list(monkeypatch):
    run = Mock(return_value=SimpleNamespace(returncode=1, stdout=""))
    monkeypatch.setattr(D.subprocess, "run", run)
    assert await D.get_model_benchmark("m") is None
    assert "m" not in D._benchmark_cache

    run.return_value = SimpleNamespace(returncode=0, stdout="[]")
    assert (await D.get_model_benchmark("m"))["tps"] == 0.0


# ── override store ───────────────────────────────────────────────────


def _redis(raw):
    r = SimpleNamespace(hget=AsyncMock(return_value=raw), hset=AsyncMock(), hdel=AsyncMock(), aclose=AsyncMock())
    return r


@pytest.mark.asyncio
async def test_override_read_non_object_falls_back_to_mirror(monkeypatch):
    D._state.overrides["m"] = {"keep_alive": "5m"}
    monkeypatch.setattr(D, "_redis_client", Mock(return_value=_redis('["junk"]')))
    assert await D._get_model_overrides("m") == {"keep_alive": "5m"}


@pytest.mark.asyncio
async def test_override_set_ignores_non_object_stored_value(monkeypatch):
    D._state.overrides["m"] = {"mode": "completion"}
    r = _redis('"junk"')
    monkeypatch.setattr(D, "_redis_client", Mock(return_value=r))
    await D._set_model_override("m", "keep_alive", "9m")
    assert D._state.overrides["m"] == {"mode": "completion", "keep_alive": "9m"}


@pytest.mark.asyncio
async def test_speculative_rejected_does_not_log_success(monkeypatch, caplog):
    monkeypatch.setattr(D, "_set_model_override", AsyncMock())
    monkeypatch.setattr(D, "_build_configure_body", AsyncMock(return_value={"model": "m"}))
    monkeypatch.setattr(D, "_post_configure", AsyncMock(return_value=400))
    with caplog.at_level("INFO", logger=D.logger.name):
        await D.configure_speculative_decoding("m", "draft")
    assert "speculative decoding configured" not in caplog.text


# ── warmup / configure payload ───────────────────────────────────────


@pytest.mark.asyncio
async def test_warmup_text_model_already_primary(monkeypatch):
    monkeypatch.setattr(D, "apply_best_practice_configs", AsyncMock())
    monkeypatch.setattr(D, "_get_vram_info", AsyncMock(return_value={"free": 20000}))
    monkeypatch.setattr(D, "_KEEP_WARM_EXTRA", [])
    monkeypatch.setattr(D.settings, "DMR_MID_MODEL", "ai/qwen3:8b", raising=False)
    monkeypatch.setattr(D.settings, "DMR_TEXT_MODEL", "ai/qwen3:8b", raising=False)
    monkeypatch.setattr(D.settings, "DMR_VISION_MODEL", "ai/qwen3-vl", raising=False)
    chat = AsyncMock()
    monkeypatch.setattr(D, "_call_dmr_chat_internal", chat)
    await D._warm_models_locked()
    warmed = [c.kwargs["model_override"] for c in chat.await_args_list]
    assert warmed == ["ai/qwen3:8b", "ai/qwen3-vl"]  # 8B not warmed twice


def test_configure_payload_minimal_cfg(monkeypatch):
    monkeypatch.setattr(D, "_BEST_PRACTICE_CONFIGS", {"ai/m": {"context_size": 4096}})
    assert D._configure_payload("ai/m") == {"model": "ai/m", "context-size": 4096}


# ── chat VRAM gate ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_chat_vram_recovers_after_first_free(monkeypatch):
    client = _chat_env(monkeypatch)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(side_effect=[False, True, True]))
    free = AsyncMock()
    drain = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    monkeypatch.setattr(D, "_wait_for_queue_drain", drain)
    assert (await D._call_dmr_chat_internal("hi"))["text"] == "hi"
    free.assert_awaited_once()
    drain.assert_not_awaited()
    assert client.post.await_args.kwargs["json"]["model"] == "model-x"


@pytest.mark.asyncio
async def test_chat_vram_drain_timeout_then_ok(monkeypatch):
    _chat_env(monkeypatch)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(side_effect=[False, False, True]))
    free = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    monkeypatch.setattr(D, "_wait_for_queue_drain", AsyncMock(return_value=False))
    assert (await D._call_dmr_chat_internal("hi"))["text"] == "hi"
    free.assert_awaited_once()  # drain timed out → no second free


@pytest.mark.asyncio
async def test_chat_vram_tiny_does_not_fit_keeps_model(monkeypatch):
    client = _chat_env(monkeypatch)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(return_value=False))
    monkeypatch.setattr(D, "_free_gpu_memory", AsyncMock())
    monkeypatch.setattr(D, "_wait_for_queue_drain", AsyncMock(return_value=False))
    monkeypatch.setattr(D.settings, "DMR_TINY_MODEL", "tiny-m", raising=False)
    assert (await D._call_dmr_chat_internal("hi"))["text"] == "hi"
    assert client.post.await_args.kwargs["json"]["model"] == "model-x"


@pytest.mark.asyncio
async def test_chat_runner_load_failure_heals_gpu(monkeypatch):
    post = AsyncMock(side_effect=[
        _resp(500, text="unable to load runner: not enough GPU memory"),
        _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
    ])
    _chat_env(monkeypatch, post=post)
    monkeypatch.setattr(D, "_model_is_cpu_pinned", lambda m: False)
    free = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    assert (await D._call_dmr_chat_internal("hi"))["text"] == "ok"
    free.assert_awaited_once()
    assert post.await_count == 2


# ── streaming ────────────────────────────────────────────────────────


class _StreamResp:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.mark.asyncio
async def test_stream_skips_keepalives_and_empty_deltas(monkeypatch):
    import app.services.gpu_arbiter as GA

    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())
    from tests.unit.test_dmr_coverage import _Slot

    monkeypatch.setattr(GA, "dmr_slot", lambda **_kw: _Slot())
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1", raising=False)
    lines = [": keep-alive", 'data: {"choices": [{"delta": {"role": "assistant"}}]}',
             'data: {"choices": [{"delta": {"content": "x"}}]}', "data: [DONE]"]
    client = SimpleNamespace(is_closed=False, stream=Mock(return_value=_StreamResp(lines)))
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    assert [c async for c in D._stream_dmr_chat({"model": "m"}, 5.0)] == ["x"]


# ── embeddings / vision VRAM recovery ────────────────────────────────


@pytest.mark.asyncio
async def test_embedding_vram_recovers_after_free(monkeypatch):
    import app.services.gpu_arbiter as GA
    from tests.unit.test_dmr_coverage import _Slot

    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())
    monkeypatch.setattr(GA, "dmr_slot", lambda **_kw: _Slot())
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(D, "_ensure_model_configured", AsyncMock())
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1", raising=False)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(side_effect=[False, True]))
    free = AsyncMock()
    drain = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    monkeypatch.setattr(D, "_wait_for_queue_drain", drain)
    client = SimpleNamespace(is_closed=False, post=AsyncMock(return_value=_resp(200, {"data": [{"embedding": [1.0]}]})))
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    assert await D.call_dmr_embedding("t") == [1.0]
    free.assert_awaited_once()
    drain.assert_not_awaited()


@pytest.mark.asyncio
async def test_vision_vram_recovers_after_unload(monkeypatch):
    post = AsyncMock(return_value=_resp(200, {"choices": [{"message": {"content": "cat"}}]}))
    _vision_env(monkeypatch, post)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(side_effect=[False, True]))
    monkeypatch.setattr(D, "_unload_idle_models", AsyncMock())
    drain = AsyncMock()
    monkeypatch.setattr(D, "_wait_for_queue_drain", drain)
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    assert await D.call_dmr_vision("aW1n", "q") == "cat"
    drain.assert_not_awaited()


def test_parse_json_unterminated_fence():
    assert D._parse_json_response('```json\n{"a": 1}') == {"a": 1}

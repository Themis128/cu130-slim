"""Coverage tests for the remaining gaps in app/services/dmr.py.

Complements test_dmr_helpers.py / test_dmr_functions.py: shared-client
lifecycle, validate_dmr_models, CLI fallback edges, VRAM probes, queue
drain, unload verification, benchmark cache, override store failure
paths, configure/keep-alive statuses, warmup lock edge cases, keep-warm
exception, chat retry/fallback internals, streaming, embeddings, vision
heal paths, request listing, JSON parsing, and startup hooks.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

import app.services.dmr as D


class _Slot:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.fixture(autouse=True)
def _reset_state():
    D._state.online = None
    D._state.last_check = 0.0
    D._state.vram = {}
    D._state.vram_check = 0.0
    D._state.overrides = {}
    D._state.override_store_down_until = 0.0
    D._state.configured = {}
    D._state.client = None
    D._state.client_loop = None
    D._state.sem = None
    D._state.sem_loop = None
    D._state.warmup_done = False
    D._state.warmup_lock_token = None
    D._benchmark_cache.clear()
    D._keep_alive_endpoint_warned.clear()
    if hasattr(D.apply_best_practice_configs, "_done"):
        del D.apply_best_practice_configs._done  # type: ignore[attr-defined]
    yield
    D._state.client = None
    D._state.client_loop = None


def _resp(status=200, json_data=None, text=""):
    return SimpleNamespace(
        status_code=status,
        text=text,
        json=lambda: json_data if json_data is not None else {},
    )


def _chat_env(monkeypatch, post=None):
    """Shared seams for _call_dmr_chat_internal tests."""
    import app.services.gpu_arbiter as GA

    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())
    monkeypatch.setattr(GA, "dmr_slot", lambda **_kw: _Slot())
    monkeypatch.setattr(D, "_select_model_by_complexity", lambda *a, **k: "model-x")
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(return_value=True))
    monkeypatch.setattr(D, "_ensure_model_configured", AsyncMock())
    monkeypatch.setattr(D, "_running_dmr_models", AsyncMock(return_value={"model-x"}))
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1", raising=False)
    if post is None:
        post = AsyncMock(return_value=_resp(200, {"choices": [{"message": {"content": "hi"}}]}))
    client = SimpleNamespace(is_closed=False, post=post)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    return client


# ── shared client lifecycle ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_client_reuse_and_recreate(monkeypatch):
    loop = asyncio.get_running_loop()
    old = SimpleNamespace(is_closed=False, aclose=AsyncMock())
    D._state.client = old
    D._state.client_loop = loop
    assert await D._get_client() is old  # reuse same-loop open client

    # loop changed → old client closed, new one built
    D._state.client_loop = object()
    new = await D._get_client()
    assert new is not old
    old.aclose.assert_awaited_once()
    await new.aclose()

    # aclose of the dead-loop client raising is swallowed
    old2 = SimpleNamespace(is_closed=False, aclose=AsyncMock(side_effect=RuntimeError))
    D._state.client = old2
    D._state.client_loop = object()
    new2 = await D._get_client()
    assert new2 is not old2
    await new2.aclose()


@pytest.mark.asyncio
async def test_close_client(monkeypatch):
    await D.close_client()  # no client → no-op
    closing = SimpleNamespace(aclose=AsyncMock(side_effect=RuntimeError))
    D._state.client = closing
    D._state.client_loop = object()
    await D.close_client()  # aclose failure swallowed, state cleared
    assert D._state.client is None and D._state.client_loop is None


def test_get_semaphore_without_running_loop():
    # Called synchronously (e.g. import-time in a thread): loop=None branch.
    D._state.sem = None
    D._state.sem_loop = object()  # not None → must be recreated
    sem = D._get_semaphore()
    assert sem is D._state.sem and D._state.sem_loop is None


# ── validate_dmr_models ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_validate_models_no_url_and_error(monkeypatch):
    monkeypatch.setattr(D.settings, "DMR_URL", "", raising=False)
    out = await D.validate_dmr_models()
    assert out["online"] is False and not out["present"]
    assert out["missing"] == out["expected"]

    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1", raising=False)
    monkeypatch.setattr(D, "_get_client", AsyncMock(side_effect=RuntimeError("down")))
    out = await D.validate_dmr_models()
    assert out["online"] is False and "down" in out["error"]


@pytest.mark.asyncio
async def test_validate_models_non200_and_matching(monkeypatch):
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1", raising=False)
    monkeypatch.setattr(D.settings, "DMR_TEXT_MODEL", "ai/llama3.2", raising=False)
    monkeypatch.setattr(D.settings, "DMR_TINY_MODEL", "ai/smollm3", raising=False)
    monkeypatch.setattr(D.settings, "DMR_MID_MODEL", "", raising=False)
    monkeypatch.setattr(D.settings, "DMR_CHATBOT_MODEL", "", raising=False)
    monkeypatch.setattr(D.settings, "DMR_EMBEDDING_MODEL", "emb", raising=False)
    monkeypatch.setattr(D.settings, "DMR_VISION_MODEL", "vl", raising=False)

    get = AsyncMock(return_value=_resp(503))
    client = SimpleNamespace(is_closed=False, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    out = await D.validate_dmr_models()
    assert out["online"] is False

    get = AsyncMock(
        return_value=_resp(
            200,
            {
                "data": [
                    {"id": "docker.io/ai/llama3.2:latest"},
                    {"id": "huggingface.co/qwen/x"},
                ]
            },
        )
    )
    client.get = get
    out = await D.validate_dmr_models()
    assert out["online"] is True
    assert "ai/llama3.2" in out["present"]
    assert "ai/smollm3" in out["missing"]
    assert "docker.io/ai/llama3.2:latest" in out["available_models"]


# ── CLI fallback edge ────────────────────────────────────────────────


def test_dmr_cli_run_generic_exception(monkeypatch):
    monkeypatch.setattr(D.subprocess, "run", Mock(side_effect=RuntimeError("boom")))
    assert D._dmr_cli_run("m", "p") is None


# ── VRAM probes ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_comfyui_vram_branches(monkeypatch):
    monkeypatch.setattr(D.settings, "COMFYUI_URL", "http://cf", raising=False)
    get = AsyncMock(return_value=_resp(500))
    client = SimpleNamespace(is_closed=False, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    assert await D._comfyui_vram() is None  # non-200

    get.return_value = _resp(200, {"devices": [{"type": "cpu"}]})
    assert await D._comfyui_vram() is None  # no cuda device

    get.return_value = _resp(200, {"devices": [{"type": "cuda", "vram_free": 0, "vram_total": 0}]})
    assert await D._comfyui_vram() is None  # zero total

    mib = 1024 * 1024
    get.return_value = _resp(200, {"devices": [{"type": "cuda", "vram_free": 4 * mib, "vram_total": 8 * mib}]})
    out = await D._comfyui_vram()
    assert out == {"used": 4, "free": 4, "total": 8}

    get.side_effect = httpx.ConnectError("x")
    assert await D._comfyui_vram() is None  # transport error


@pytest.mark.asyncio
async def test_get_vram_info_cache_and_miss(monkeypatch):
    D._state.vram = {"free": 9}
    import time

    D._state.vram_check = time.monotonic()
    assert await D._get_vram_info() == {"free": 9}  # cache hit

    D._state.vram_check = 0.0
    monkeypatch.setattr(D, "_nvidia_smi_vram", lambda: None)
    monkeypatch.setattr(D, "_comfyui_vram", AsyncMock(return_value=None))
    assert await D._get_vram_info() is None  # both probes fail → None
    assert D._state.vram == {}


@pytest.mark.asyncio
async def test_queued_load_vram_target_variants(monkeypatch):
    # queued load whose name matches the target is skipped
    get = AsyncMock(
        return_value=_resp(
            200,
            {
                "models": [
                    {"name": "ai/target:latest", "expires_at": "0001-01-01T00:00:00Z"},
                    {"name": "ai/other-8b:latest", "expires_at": "0001-01-01T00:00:00Z"},
                ]
            },
        )
    )
    client = SimpleNamespace(is_closed=False, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    out = await D._queued_load_vram("ai/target")
    assert out == 4096  # only the foreign 8b queued load counts

    # target already resident (real expires_at) → -1
    get.return_value = _resp(200, {"models": [{"name": "ai/target:latest", "expires_at": "2026-01-01T00:00:00Z"}]})
    assert await D._queued_load_vram("ai/target") == -1


@pytest.mark.asyncio
async def test_has_vram_for_model_short_circuits(monkeypatch):
    monkeypatch.setattr(D, "_model_is_cpu_pinned", lambda m: False)
    monkeypatch.setattr(D, "_queued_load_vram", AsyncMock(return_value=-1))
    assert await D._has_vram_for_model("m") is True  # already resident

    monkeypatch.setattr(D, "_queued_load_vram", AsyncMock(return_value=0))
    monkeypatch.setattr(D, "_get_vram_info", AsyncMock(return_value=None))
    assert await D._has_vram_for_model("m") is True  # unmeasurable → allow


@pytest.mark.asyncio
async def test_wait_for_queue_drain(monkeypatch):
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    seq = iter([100, 0])
    monkeypatch.setattr(D, "_queued_load_vram", AsyncMock(side_effect=lambda m: next(seq)))
    assert await D._wait_for_queue_drain("m") is True

    monkeypatch.setattr(D, "_queued_load_vram", AsyncMock(return_value=100))
    assert await D._wait_for_queue_drain("m", max_wait=3.0) is False


# ── unload verification ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unload_idle_models_verify_paths(monkeypatch):
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    post = AsyncMock(side_effect=httpx.RemoteProtocolError("dropped"))
    get = AsyncMock(return_value=_resp(200, {"models": [{"name": "m"}]}))
    client = SimpleNamespace(is_closed=False, post=post, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    await D._unload_idle_models()  # transport blip → verify → still listed
    get.assert_awaited_once()

    # verify probe itself fails → swallowed
    get.side_effect = httpx.ConnectError("x")
    await D._unload_idle_models()

    # unload POST non-200 → verify path too
    post.side_effect = None
    post.return_value = _resp(500)
    get.side_effect = None
    get.return_value = _resp(200, {"models": []})
    await D._unload_idle_models()

    # generic POST failure → early return, no verify
    post.side_effect = ValueError("weird")
    get.reset_mock()
    await D._unload_idle_models()
    get.assert_not_awaited()


# ── benchmark cache ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_model_benchmark(monkeypatch):
    import time

    D._benchmark_cache["m"] = {"tps": 5.0, "timestamp": time.time()}
    assert (await D.get_model_benchmark("m"))["tps"] == 5.0  # cache hit

    run = Mock(return_value=SimpleNamespace(returncode=0, stdout='[{"tokens_per_second": 42.0}]'))
    monkeypatch.setattr(D.subprocess, "run", run)
    out = await D.get_model_benchmark("m2")
    assert out["tps"] == 42.0

    D._benchmark_cache.clear()
    run.return_value = SimpleNamespace(returncode=0, stdout='{"x": 1}')
    assert (await D.get_model_benchmark("m3"))["tps"] == 0.0  # dict shape

    run.side_effect = RuntimeError("no docker")
    assert await D.get_model_benchmark("m4") is None


# ── override store failure paths ─────────────────────────────────────


@pytest.mark.asyncio
async def test_set_model_override_store_failure(monkeypatch):
    D._state.overrides["m"] = {"keep_alive": "5m"}
    monkeypatch.setattr(D, "_redis_client", Mock(side_effect=RuntimeError("no redis")))
    await D._set_model_override("m", "keep_alive", None)  # line 753
    assert D._state.overrides.get("m") is None

    D._state.overrides["m"] = {}
    D._state.override_store_down_until = 0.0  # re-arm the store attempt
    await D._set_model_override("m", "keep_alive", "9m")  # line 755
    assert D._state.overrides["m"] == {"keep_alive": "9m"}


@pytest.mark.asyncio
async def test_post_configure_and_keep_alive_statuses(monkeypatch):
    monkeypatch.setattr(D, "_get_client", AsyncMock(side_effect=RuntimeError("x")))
    assert await D._post_configure("m", {}) is None  # exc → None

    post = AsyncMock(return_value=_resp(404))
    client = SimpleNamespace(is_closed=False, post=post)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    assert await D._post_configure("m", {}) == 404  # non-200 status

    # 404 on keep_alive → warned once
    monkeypatch.setattr(D, "_set_model_override", AsyncMock())
    monkeypatch.setattr(D, "_build_configure_body", AsyncMock(return_value={"model": "m"}))
    await D.configure_keep_alive("m")
    assert "m" in D._keep_alive_endpoint_warned
    D._keep_alive_endpoint_warned.add("m")
    await D.configure_keep_alive("m")  # second call → no re-warn

    # 200 → success log path
    post.return_value = _resp(200)
    await D.configure_keep_alive("m")


@pytest.mark.asyncio
async def test_configure_speculative_decoding(monkeypatch):
    post = AsyncMock(return_value=_resp(200))
    client = SimpleNamespace(is_closed=False, post=post)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(D, "_set_model_override", AsyncMock())
    monkeypatch.setattr(D, "_build_configure_body", AsyncMock(return_value={"model": "m"}))
    await D.configure_speculative_decoding("m", "draft")
    post.assert_awaited_once()


# ── warmup lock edges ────────────────────────────────────────────────


def test_is_warmup_done():
    D._state.warmup_done = False
    assert D.is_warmup_done() is False
    D._state.warmup_done = True
    assert D.is_warmup_done() is True
    D.reset_warmup()
    assert D.is_warmup_done() is False


@pytest.mark.asyncio
async def test_release_warmup_lock_failure(monkeypatch):
    D._state.warmup_lock_token = "tok"
    r = SimpleNamespace(eval=AsyncMock(side_effect=RuntimeError), aclose=AsyncMock())
    monkeypatch.setattr(D, "_redis_client", Mock(return_value=r))
    await D._release_warmup_lock()  # eval failure swallowed
    assert D._state.warmup_lock_token is None


@pytest.mark.asyncio
async def test_warmup_models_inner_done_check(monkeypatch):
    """warmup_done flipping between the outer check and the lock → return."""

    class _FlipLock:
        async def __aenter__(self):
            D._state.warmup_done = True
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(D, "_warmup_lock", _FlipLock())
    health = AsyncMock()
    monkeypatch.setattr(D, "_check_dmr_health", health)
    await D.warmup_models()
    health.assert_not_awaited()  # returned before the health check


@pytest.mark.asyncio
async def test_warm_models_locked_warm_failure(monkeypatch):
    monkeypatch.setattr(D, "apply_best_practice_configs", AsyncMock())
    monkeypatch.setattr(D, "_get_vram_info", AsyncMock(return_value=None))
    monkeypatch.setattr(D.settings, "DMR_MID_MODEL", "mid-model", raising=False)
    monkeypatch.setattr(D.settings, "DMR_TINY_MODEL", "tiny", raising=False)
    monkeypatch.setattr(D.settings, "DMR_TEXT_MODEL", "ai/qwen3:8b-x", raising=False)
    monkeypatch.setattr(D.settings, "DMR_VISION_MODEL", "ai/qwen3-vl", raising=False)
    chat = AsyncMock(side_effect=RuntimeError("load failed"))
    monkeypatch.setattr(D, "_call_dmr_chat_internal", chat)
    await D._warm_models_locked()  # per-model failure swallowed (1016-1017)
    chat.assert_awaited_once()  # VRAM unmeasurable → only the mid model


# ── running models + keep-warm exception ─────────────────────────────


@pytest.mark.asyncio
async def test_running_dmr_models(monkeypatch):
    get = AsyncMock(return_value=_resp(500))
    client = SimpleNamespace(is_closed=False, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    assert await D._running_dmr_models() == set()  # non-200

    get.return_value = _resp(200, {"models": [{"name": "hf.co/AI/Qwen3:latest"}, {"name": "m2"}]})
    out = await D._running_dmr_models()
    assert "huggingface.co/ai/qwen3:latest" in out and "m2" in out

    get.side_effect = httpx.ConnectError("x")
    assert await D._running_dmr_models() == set()  # exception → empty


@pytest.mark.asyncio
async def test_keep_warm_models_ping_failure(monkeypatch):
    import app.services.gpu_arbiter as GA

    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(GA, "media_gpu_busy", AsyncMock(return_value=False))
    monkeypatch.setattr(D, "_running_dmr_models", AsyncMock(return_value={"m1"}))
    monkeypatch.setattr(D, "_keep_warm_models", lambda: ["m1"])
    monkeypatch.setattr(D, "_call_dmr_chat_internal", AsyncMock(side_effect=RuntimeError("ping fail")))
    out = await D.keep_warm_models()
    assert out == {"warmed": [], "skipped": ["m1"]}


# ── best-practice configs idempotency ────────────────────────────────


@pytest.mark.asyncio
async def test_apply_best_practice_configs_done(monkeypatch):
    D.apply_best_practice_configs._done = True  # type: ignore[attr-defined]
    ensure = AsyncMock()
    monkeypatch.setattr(D, "_ensure_model_configured", ensure)
    await D.apply_best_practice_configs()  # early return
    ensure.assert_not_awaited()


# ── _call_dmr_chat_internal internals ────────────────────────────────


@pytest.mark.asyncio
async def test_chat_offline_cli_fallback(monkeypatch):
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(return_value=False))
    monkeypatch.setattr(D, "_select_model_by_complexity", lambda *a, **k: "model-x")
    import app.services.gpu_arbiter as GA

    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())
    cli = Mock(return_value="cli answer")
    monkeypatch.setattr(D, "_dmr_cli_run", cli)
    out = await D._call_dmr_chat_internal("hi")
    assert out == {"text": "cli answer"}

    # schema + CLI → parsed JSON
    cli.return_value = '{"a": 1}'
    out = await D._call_dmr_chat_internal("hi", schema={"type": "object"})
    assert out == {"a": 1}

    # CLI failure → ConnectionError
    cli.return_value = None
    with pytest.raises(ConnectionError, match="offline"):
        await D._call_dmr_chat_internal("hi")


@pytest.mark.asyncio
async def test_chat_payload_flags(monkeypatch):
    client = _chat_env(monkeypatch)
    monkeypatch.setattr(D, "_select_model_by_complexity", lambda *a, **k: "ai/qwen3-mid")
    tools = [{"type": "function", "function": {"name": "f"}}]
    await D._call_dmr_chat_internal("hi", top_k=40, tools=tools, max_tokens=None)
    payload = client.post.await_args.kwargs["json"]
    assert payload["top_k"] == 40
    assert payload["tools"] == tools
    assert payload["max_tokens"] == 4096  # default when not given
    assert payload["messages"][1]["content"].endswith("/no_think")  # qwen3


@pytest.mark.asyncio
async def test_chat_tool_calls_response(monkeypatch):
    post = AsyncMock(return_value=_resp(200, {"choices": [{"message": {"content": "<think>t</think>", "tool_calls": [{"id": "c1"}]}}]}))
    _chat_env(monkeypatch, post=post)
    out = await D._call_dmr_chat_internal("hi", tools=[{"x": 1}])
    assert out == {"tool_calls": [{"id": "c1"}], "text": ""}


@pytest.mark.asyncio
async def test_chat_vram_pressure_tiny_fallback(monkeypatch):
    calls = iter([False, False, False, True])
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    client = _chat_env(monkeypatch)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(side_effect=lambda m: next(calls)))
    free = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    monkeypatch.setattr(D, "_wait_for_queue_drain", AsyncMock(return_value=True))
    monkeypatch.setattr(D.settings, "DMR_TINY_MODEL", "tiny-m", raising=False)
    monkeypatch.setattr(D, "_running_dmr_models", AsyncMock(return_value={"tiny-m"}))
    out = await D._call_dmr_chat_internal("hi")
    assert out["text"] == "hi"
    assert client.post.await_args.kwargs["json"]["model"] == "tiny-m"
    assert free.await_count == 2


@pytest.mark.asyncio
async def test_chat_stream_return(monkeypatch):
    _chat_env(monkeypatch)
    out = await D._call_dmr_chat_internal("hi", stream=True)
    assert "stream" in out  # generator created lazily


@pytest.mark.asyncio
async def test_chat_retry_cpu_pinned_timeout(monkeypatch):
    post = AsyncMock(side_effect=httpx.TimeoutException("t"))
    _chat_env(monkeypatch, post=post)
    monkeypatch.setattr(D, "_model_is_cpu_pinned", lambda m: True)
    free = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    with pytest.raises(ConnectionError, match="failed after retry"):
        await D._call_dmr_chat_internal("hi")
    free.assert_not_awaited()  # cpu-pinned: no GPU heal on timeout


@pytest.mark.asyncio
async def test_chat_retry_timeout_frees_gpu(monkeypatch):
    post = AsyncMock(side_effect=httpx.TimeoutException("t"))
    _chat_env(monkeypatch, post=post)
    monkeypatch.setattr(D, "_model_is_cpu_pinned", lambda m: False)
    free = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    with pytest.raises(ConnectionError, match="failed after retry"):
        await D._call_dmr_chat_internal("hi")
    free.assert_awaited_once()  # attempt-0 timeout → GPU heal before retry


@pytest.mark.asyncio
async def test_chat_connect_error_cli_text(monkeypatch):
    post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    _chat_env(monkeypatch, post=post)
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(D, "_dmr_cli_run", Mock(return_value="cli out"))
    out = await D._call_dmr_chat_internal("hi")
    assert out == {"text": "cli out"}  # attempt-1 ConnectError → CLI text


@pytest.mark.asyncio
async def test_chat_connect_error_cli_schema(monkeypatch):
    post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    _chat_env(monkeypatch, post=post)
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    monkeypatch.setattr(D, "_dmr_cli_run", Mock(return_value='{"k": 2}'))
    out = await D._call_dmr_chat_internal("hi", schema={"type": "object"})
    assert out == {"k": 2}  # attempt-1 ConnectError → CLI + parse


@pytest.mark.asyncio
async def test_chat_generic_exception_propagates(monkeypatch):
    def _bad_json():
        raise ValueError("not json")

    post = AsyncMock(return_value=SimpleNamespace(status_code=200, text="", json=_bad_json))
    _chat_env(monkeypatch, post=post)
    with pytest.raises(ValueError):
        await D._call_dmr_chat_internal("hi")


# ── streaming ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stream_dmr_chat(monkeypatch):
    import app.services.gpu_arbiter as GA

    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())
    monkeypatch.setattr(GA, "dmr_slot", lambda **_kw: _Slot())
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1", raising=False)

    class _StreamResp:
        def __init__(self, lines, status=200):
            self.status_code = status
            self._lines = lines

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    lines = [
        'data: {"choices": [{"delta": {"content": "he"}}]}',
        'data: {"choices": [{"delta": {"content": "llo"}}]}',
        "data: not-json",
        "data: [DONE]",
        'data: {"choices": [{"delta": {"content": "late"}}]}',
        "ignored line",
    ]
    resp = _StreamResp(lines)
    stream = Mock(return_value=resp)
    client = SimpleNamespace(is_closed=False, stream=stream)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    chunks = [c async for c in D._stream_dmr_chat({"model": "m"}, 10.0)]
    assert chunks == ["he", "llo"]
    assert stream.await_count == 1 or stream.call_count == 1

    # non-200 → ConnectionError
    client.stream = Mock(return_value=_StreamResp([], status=500))
    with pytest.raises(ConnectionError, match="stream error 500"):
        [c async for c in D._stream_dmr_chat({"model": "m"}, 10.0)]


# ── embeddings ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_dmr_embedding(monkeypatch):
    import app.services.gpu_arbiter as GA

    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())
    monkeypatch.setattr(GA, "dmr_slot", lambda **_kw: _Slot())
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(D, "_ensure_model_configured", AsyncMock())
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1", raising=False)
    monkeypatch.setattr(D.settings, "DMR_EMBEDDING_MODEL", "emb", raising=False)

    # VRAM gate: insufficient → free → still insufficient → drain
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(side_effect=[False, False]))
    free = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    drain = AsyncMock()
    monkeypatch.setattr(D, "_wait_for_queue_drain", drain)
    post = AsyncMock(return_value=_resp(200, {"data": [{"embedding": [0.1, 0.2]}]}))
    client = SimpleNamespace(is_closed=False, post=post)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    out = await D.call_dmr_embedding("txt")
    assert out == [0.1, 0.2]
    free.assert_awaited_once()
    drain.assert_awaited_once()

    # non-200 → ConnectionError
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(return_value=True))
    post.return_value = _resp(500, text="oops")
    with pytest.raises(ConnectionError, match="embedding error 500"):
        await D.call_dmr_embedding("txt")

    # transport failure → ConnectionError
    post.side_effect = httpx.TimeoutException("t")
    with pytest.raises(ConnectionError, match="connection error"):
        await D.call_dmr_embedding("txt")

    # DMR offline → ConnectionError before any request
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(return_value=False))
    with pytest.raises(ConnectionError, match="offline"):
        await D.call_dmr_embedding("txt")


# ── vision heal paths ────────────────────────────────────────────────


def _vision_env(monkeypatch, post):
    import app.services.gpu_arbiter as GA

    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())
    monkeypatch.setattr(GA, "dmr_slot", lambda **_kw: _Slot())
    monkeypatch.setattr(D, "_ensure_model_configured", AsyncMock())
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1", raising=False)
    monkeypatch.setattr(D.settings, "DMR_VISION_MODEL", "qwen3-vl", raising=False)
    client = SimpleNamespace(is_closed=False, post=post)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    return client


@pytest.mark.asyncio
async def test_vision_vram_insufficient(monkeypatch):
    post = AsyncMock(return_value=_resp(200, {"choices": [{"message": {"content": "a red square"}}]}))
    _vision_env(monkeypatch, post)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(side_effect=[False, False]))
    unload = AsyncMock()
    monkeypatch.setattr(D, "_unload_idle_models", unload)
    drain = AsyncMock()
    monkeypatch.setattr(D, "_wait_for_queue_drain", drain)
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    out = await D.call_dmr_vision("aW1hZ2U=", "what is this")
    assert out == "a red square"
    unload.assert_awaited_once()
    drain.assert_awaited_once()


@pytest.mark.asyncio
async def test_vision_timeout_heal_retry(monkeypatch):
    post = AsyncMock(
        side_effect=[
            httpx.TimeoutException("cold"),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
    )
    _vision_env(monkeypatch, post)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(return_value=True))
    monkeypatch.setattr(D, "_model_is_cpu_pinned", lambda m: False)
    free = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    out = await D.call_dmr_vision("aW1hZ2U=", "q")
    assert out == "ok"
    free.assert_awaited_once()  # healed between attempts


@pytest.mark.asyncio
async def test_vision_5xx_heal_and_failure(monkeypatch):
    post = AsyncMock(
        side_effect=[
            _resp(503, text="loading"),
            _resp(200, {"choices": [{"message": {"content": "ok"}}]}),
        ]
    )
    _vision_env(monkeypatch, post)
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(return_value=True))
    monkeypatch.setattr(D, "_model_is_cpu_pinned", lambda m: False)
    free = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", free)
    out = await D.call_dmr_vision("aW1hZ2U=", "q")
    assert out == "ok"

    # non-5xx error → break → None
    post.side_effect = None
    post.return_value = _resp(400, text="bad")
    assert await D.call_dmr_vision("aW1hZ2U=", "q") is None

    # exception → None (graceful)
    post.side_effect = RuntimeError("weird")
    assert await D.call_dmr_vision("aW1hZ2U=", "q") is None


# ── request listing ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_dmr_requests(monkeypatch):
    get = AsyncMock(return_value=_resp(500))
    client = SimpleNamespace(is_closed=False, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    assert await D.get_dmr_requests() == []  # non-200

    get.return_value = _resp(
        200,
        [
            {
                "records": [
                    {"model": "ai/m1", "timestamp": "2"},
                    {"model": "ai/m2", "timestamp": "5"},
                    {"model": "ai/m1", "timestamp": "9"},
                ]
            },
            {"records": None},
        ],
    )
    out = await D.get_dmr_requests()
    assert [r["timestamp"] for r in out] == ["9", "5", "2"]  # newest first

    out = await D.get_dmr_requests(model="m1", limit=1)
    assert out == [{"model": "ai/m1", "timestamp": "9"}]

    get.side_effect = httpx.ConnectError("x")
    assert await D.get_dmr_requests() == []  # exception → []


# ── JSON parsing + startup ───────────────────────────────────────────


def test_parse_json_response_bad_fenced():
    # ``` fence whose contents match \{...\} but aren't valid JSON
    out = D._parse_json_response("```json\n{not valid json}\n```")
    assert out["_parse_error"] is True
    assert "{not valid json}" in out["text"]


def test_to_vision_safe_uri_bad_image():
    # non-safe mime with undecodable bytes → transcode fails → sent as-is
    uri = "data:image/webp;base64,bm90LWEtcGljdHVyZQ=="
    assert D._to_vision_safe_uri(uri) == uri


@pytest.mark.asyncio
async def test_on_startup_schedules_warmup(monkeypatch):
    created = Mock()
    monkeypatch.setattr(D.asyncio, "create_task", created)
    monkeypatch.setattr(D, "warmup_models", Mock(return_value=object()))
    await D.on_startup()
    created.assert_called_once()


@pytest.mark.asyncio
async def test_on_shutdown(monkeypatch):
    release = AsyncMock()
    close = AsyncMock()
    monkeypatch.setattr(D, "_release_warmup_lock", release)
    monkeypatch.setattr(D, "close_client", close)
    await D.on_shutdown()
    release.assert_awaited_once()
    close.assert_awaited_once()

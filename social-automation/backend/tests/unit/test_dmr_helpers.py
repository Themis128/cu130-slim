"""Unit tests for app/services/dmr.py — helpers, VRAM gate, config, keep-warm.

Complements the call-path suites (test_dmr_*) with the pure helper layer
(model VRAM estimates, task families, complexity routing, configure
payloads, JSON/vision helpers) and the async orchestration seams
(health cache, nvidia-smi/ComfyUI VRAM probes, queued-load accounting,
model overrides, keep-warm, warmup lock).
"""

from __future__ import annotations

import base64
import io
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.services.dmr as D


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    D._state.online = None
    D._state.last_check = 0.0
    D._state.vram = {}
    D._state.vram_check = 0.0
    D._state.overrides = {}
    D._state.override_store_down_until = 0.0
    D._state.configured = {}
    yield


# ── pure helpers ─────────────────────────────────────────────────────


def test_strip_think_tags():
    assert D._strip_think_tags("<think>reasoning</think>answer") == "answer"
    assert D._strip_think_tags("  plain  ") == "plain"
    assert D._strip_think_tags(
        "a<think>x\ny</think>b<think>z</think>c") == "abc"


def test_dmr_base_url(monkeypatch):
    monkeypatch.setattr(D.settings, "DMR_BASE_URL", "",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_URL",
                        "http://dmr:12434/engines/llama.cpp/v1",
                        raising=False)
    assert D._dmr_base_url() == "http://dmr:12434"
    monkeypatch.setattr(D.settings, "DMR_BASE_URL", "http://x:1/",
                        raising=False)
    assert D._dmr_base_url() == "http://x:1"


def test_model_vram_mb():
    assert D._model_vram_mb("ai/smollm2") == 512
    assert D._model_vram_mb("ai/smollm3") == 2048
    assert D._model_vram_mb("qwen3-embedding-0.6b") == 768
    assert D._model_vram_mb("ai/qwen3-embedding") == 4096
    assert D._model_vram_mb("ai/qwen3-vl") == 4096
    assert D._model_vram_mb("ai/qwen3:8b") == 4096
    assert D._model_vram_mb("qwen3-4b") == 3072
    assert D._model_vram_mb("ai/llama3.2") == 2048
    assert D._model_vram_mb("unknown-model") == 2048


def test_model_task_family_and_coerce():
    assert D._model_task_family("qwen3-embedding-0.6b") == "embedding"
    assert D._model_task_family("ai/qwen3-vl") == "vision"
    # "vllm" must NOT match the vl family
    assert D._model_task_family("ai/smollm2-vllm") == "text"
    assert D._model_task_family("ai/llama3.2") == "text"

    assert D._coerce_model_for_task("ai/qwen3-vl", "vision", "fb") == \
        "ai/qwen3-vl"
    assert D._coerce_model_for_task("ai/llama3.2", "vision", "fb") == "fb"
    assert D._coerce_model_for_task(None, "text", "fb") == "fb"
    assert D._coerce_model_for_task(
        "qwen3-embedding-0.6b", "text", "fb") == "fb"


def test_select_model_by_complexity(monkeypatch):
    monkeypatch.setattr(D.settings, "DMR_TEXT_MODEL", "text-8b",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_MID_MODEL", "mid-4b",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_TINY_MODEL", "tiny",
                        raising=False)
    # short-form platform → mid
    assert D._select_model_by_complexity("p", platform="tiktok") == "mid-4b"
    # long-form → text
    assert D._select_model_by_complexity(
        "p", platform="linkedin") == "text-8b"
    # schema w/o platform → text
    assert D._select_model_by_complexity("p", schema={"t": 1}) == "text-8b"
    # short prompt no platform → tiny
    assert D._select_model_by_complexity("hi") == "tiny"
    # long prompt no hint → text
    assert D._select_model_by_complexity("x" * 300) == "text-8b"
    # matching override wins; mismatched gets coerced to routed
    assert D._select_model_by_complexity(
        "hi", model_override="custom-text") == "custom-text"
    assert D._select_model_by_complexity(
        "hi", model_override="qwen3-vl") == "tiny"


def test_configure_payload_and_cpu_pin():
    body = D._configure_payload("ai/qwen3:8b-q4_K_M")
    assert body["context-size"] == 6144 and body["keep_alive"] == "2m"
    assert body["llamacpp"] == {"reasoning-budget": -1}
    # short-ref tolerance
    body = D._configure_payload("qwen3:8b-q4_K_M")
    assert body is not None
    # embedding mode flag
    body = D._configure_payload("hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF")
    assert body["mode"] == "embedding"
    # unknown model → None
    assert D._configure_payload("unknown/thing") is None

    # every canonical model is CPU-pinned
    for m in D._BEST_PRACTICE_CONFIGS:
        assert D._model_is_cpu_pinned(m), m
    assert not D._model_is_cpu_pinned("unknown/thing")


def test_merge_overrides():
    base = {"model": "m", "keep_alive": "2m"}
    out = D._merge_overrides(base, {"keep_alive": "10m",
                                    "speculative": {"model": "s"}})
    assert out["keep_alive"] == "10m"
    assert out["speculative"] == {"model": "s"}
    assert base["keep_alive"] == "2m"  # input not mutated
    assert D._merge_overrides(base, {}) == base


def test_vision_helpers():
    from PIL import Image

    # RGBA → flattened on white
    rgba = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
    out = D._flatten_alpha(rgba)
    assert out.mode == "RGB"
    px = out.getpixel((4, 4))
    assert px == (255, 255, 255)  # transparent → white, not black

    # RGB passthrough
    rgb = Image.new("RGB", (8, 8))
    assert D._flatten_alpha(rgb) is rgb

    # safe mime → untouched
    uri = "data:image/jpeg;base64,QUJD"
    assert D._to_vision_safe_uri(uri) == uri
    # malformed → untouched
    assert D._to_vision_safe_uri("not-a-uri") == "not-a-uri"
    # webp → transcoded to png
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "WEBP")
    webp_uri = "data:image/webp;base64," + base64.b64encode(
        buf.getvalue()).decode()
    out = D._to_vision_safe_uri(webp_uri)
    assert out.startswith("data:image/png;base64,")
    from PIL import Image as I2
    img = I2.open(io.BytesIO(
        base64.b64decode(out.split(",", 1)[1])))
    assert img.format == "PNG"


def test_parse_json_response_dmr():
    assert D._parse_json_response('{"a": 1}') == {"a": 1}
    assert D._parse_json_response('```json\n{"a": 2}\n```') == {"a": 2}
    assert D._parse_json_response('prose {"a": 3} end') == {"a": 3}
    out = D._parse_json_response("not json")
    assert out["_parse_error"] is True and out["text"] == "not json"


# ── health + vram probes ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_dmr_health(monkeypatch):
    # no URL → offline
    monkeypatch.setattr(D.settings, "DMR_URL", "", raising=False)
    assert await D._check_dmr_health() is False
    assert D._state.online is False

    # healthy → cached
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1",
                        raising=False)
    client = SimpleNamespace(is_closed=False, get=AsyncMock(
        return_value=SimpleNamespace(status_code=200)))
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    D._state.online = None
    assert await D._check_dmr_health() is True
    # cached — get not called again
    client.get.reset_mock()
    assert await D._check_dmr_health() is True
    client.get.assert_not_called()

    # invalidate → re-probe, transport error → offline
    D._invalidate_health_cache()
    client.get = AsyncMock(side_effect=Exception("down"))
    assert await D._check_dmr_health() is False


@pytest.mark.asyncio
async def test_vram_probes(monkeypatch):
    # nvidia-smi happy path
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="2048, 6144, 8192"))
    assert D._nvidia_smi_vram() == {
        "used": 2048, "free": 6144, "total": 8192}
    # failure → None
    monkeypatch.setattr(D.subprocess, "run",
                        lambda *a, **k: SimpleNamespace(
                            returncode=1, stdout=""))
    assert D._nvidia_smi_vram() is None
    monkeypatch.setattr(D.subprocess, "run",
                        Mock(side_effect=FileNotFoundError()))
    assert D._nvidia_smi_vram() is None

    # comfyui fallback — cuda device vram math
    monkeypatch.setattr(D.settings, "COMFYUI_URL", "http://comfy",
                        raising=False)
    mib = 1024 * 1024
    client = SimpleNamespace(is_closed=False, get=AsyncMock(return_value=
        SimpleNamespace(status_code=200, json=lambda: {"devices": [
            {"type": "cuda", "vram_free": 5 * mib,
             "vram_total": 8 * mib}]})))
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    out = await D._comfyui_vram()
    assert out == {"used": 3, "free": 5, "total": 8}  # MiB

    # no comfyui url → None
    monkeypatch.setattr(D.settings, "COMFYUI_URL", "", raising=False)
    assert await D._comfyui_vram() is None

    # _get_vram_info: smi first, comfy fallback, caching
    monkeypatch.setattr(D, "_nvidia_smi_vram",
                        lambda: {"used": 1, "free": 2, "total": 3})
    out = await D._get_vram_info()
    assert out["total"] == 3
    assert D._state.vram_check > 0


@pytest.mark.asyncio
async def test_queued_load_vram(monkeypatch):
    monkeypatch.setattr(D.settings, "DMR_BASE_URL", "http://dmr",
                        raising=False)
    ps = {"models": [
        # queued load (epoch-0 expires_at) on another model → counts
        {"name": "ai/qwen3:8b-q4_K_M", "expires_at": "0001-01-01T00:00:00Z"},
        # resident model (real expiry) → doesn't count
        {"name": "ai/llama3.2", "expires_at": "2026-01-01T00:00:00Z"},
    ]}
    client = SimpleNamespace(is_closed=False, get=AsyncMock(
        return_value=SimpleNamespace(status_code=200, json=lambda: ps)))
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    reserved = await D._queued_load_vram("ai/smollm3")
    assert reserved == 4096  # only the queued 8B

    # target itself resident → -1 (no load needed at all)
    reserved = await D._queued_load_vram("ai/llama3.2")
    assert reserved == -1

    # unreachable → 0
    client.get = AsyncMock(side_effect=Exception("down"))
    assert await D._queued_load_vram("ai/x") == 0


@pytest.mark.asyncio
async def test_has_vram_for_model(monkeypatch):
    # CPU-pinned models never gate
    assert await D._has_vram_for_model("ai/llama3.2") is True


# ── overrides store ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_overrides(monkeypatch):
    # redis happy path — filters non-override fields
    r = SimpleNamespace(
        hget=AsyncMock(return_value=json.dumps(
            {"keep_alive": "9m", "bogus": 1})),
        hset=AsyncMock(), hdel=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(D, "_redis_client", lambda: r)
    out = await D._get_model_overrides("m1")
    assert out == {"keep_alive": "9m"}
    assert D._state.overrides["m1"] == {"keep_alive": "9m"}

    # set → merge + hset; remove → hdel when empty
    await D._set_model_override("m1", "keep_alive", "12m")
    assert r.hset.await_args.args[2] == json.dumps({"keep_alive": "12m"})
    await D._set_model_override("m1", "keep_alive", None)
    r.hdel.assert_awaited_once()

    # redis down → local mirror + backoff
    r.hget = AsyncMock(side_effect=ConnectionError("down"))
    D._state.overrides["m2"] = {"keep_alive": "5m"}
    out = await D._get_model_overrides("m2")
    assert out == {"keep_alive": "5m"}
    assert not D._override_store_available()

    # store-down path writes to local mirror only
    await D._set_model_override("m3", "keep_alive", "7m")
    assert D._state.overrides["m3"]["keep_alive"] == "7m"

    await D.clear_model_overrides("m3")
    assert "m3" not in D._state.overrides


@pytest.mark.asyncio
async def test_ensure_model_configured(monkeypatch):
    # unknown model + no overrides → no-op
    post = AsyncMock()
    monkeypatch.setattr(D, "_post_configure", post)
    await D._ensure_model_configured("unknown/thing")
    post.assert_not_awaited()

    # known model → pushes merged body
    monkeypatch.setattr(D, "_get_model_overrides", AsyncMock(
        return_value={"keep_alive": "99m"}))
    await D._ensure_model_configured("ai/llama3.2", force=True)
    body = post.await_args.args[1]
    assert body["keep_alive"] == "99m" and body["context-size"] == 4096

    # TTL — second call within window skips
    import time
    D._state.configured["ai/llama3.2"] = time.monotonic()
    post.reset_mock()
    await D._ensure_model_configured("ai/llama3.2")
    post.assert_not_awaited()


# ── cli fallback + validate + keep-warm ──────────────────────────────


def test_dmr_cli_run(monkeypatch):
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=0, stdout="  answer  "))
    assert D._dmr_cli_run("m", "p") == "answer"
    monkeypatch.setattr(D.subprocess, "run", lambda *a, **k: SimpleNamespace(
        returncode=1, stdout=""))
    assert D._dmr_cli_run("m", "p") is None
    monkeypatch.setattr(D.subprocess, "run",
                        Mock(side_effect=subprocess.TimeoutExpired("c", 1)))
    assert D._dmr_cli_run("m", "p") is None
    monkeypatch.setattr(D.subprocess, "run",
                        Mock(side_effect=FileNotFoundError()))
    assert D._dmr_cli_run("m", "p") is None


@pytest.mark.asyncio
async def test_validate_dmr_models(monkeypatch):
    monkeypatch.setattr(D.settings, "DMR_TEXT_MODEL", "m-text",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_TINY_MODEL", "m-tiny",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_MID_MODEL", "m-mid",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_CHATBOT_MODEL", "",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_EMBEDDING_MODEL", "m-emb",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_VISION_MODEL", "m-vis",
                        raising=False)
    client = SimpleNamespace(is_closed=False, get=AsyncMock(return_value=
        SimpleNamespace(status_code=200, json=lambda: {"data": [
            {"id": "m-text"}, {"id": "m-tiny"}]})))
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    out = await D.validate_dmr_models()
    assert out["online"] is True
    assert "m-text" in out["present"]
    assert "m-vis" in out["missing"]

    # offline → online False
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(
        return_value=False))
    client.get = AsyncMock(
        return_value=SimpleNamespace(status_code=500, json=dict))
    out = await D.validate_dmr_models()
    assert out["online"] is False


@pytest.mark.asyncio
async def test_keep_warm_models(monkeypatch):
    # dmr offline → early reason
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(
        return_value=False))
    out = await D.keep_warm_models()
    assert out["reason"] == "dmr_offline"

    # media job owns GPU → skip
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(
        return_value=True))
    import app.services.gpu_arbiter as GA
    monkeypatch.setattr(GA, "media_gpu_busy", AsyncMock(
        return_value=True))
    out = await D.keep_warm_models()
    assert out["reason"] == "media_job"

    # resident model → pinged warm; non-resident + no vram → skipped
    monkeypatch.setattr(GA, "media_gpu_busy", AsyncMock(
        return_value=False))
    monkeypatch.setattr(D, "_running_dmr_models", AsyncMock(
        return_value={"m-mid"}))
    monkeypatch.setattr(D, "_keep_warm_models", lambda: ["m-mid", "m-cold"])
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(
        return_value=False))
    chat = AsyncMock(return_value={})
    monkeypatch.setattr(D, "_call_dmr_chat_internal", chat)
    out = await D.keep_warm_models()
    assert out["warmed"] == ["m-mid"] and out["skipped"] == ["m-cold"]
    assert chat.await_args.kwargs["max_tokens"] == 1


# ── unload / gpu-release / warmup orchestration ──────────────────────


class _Slot:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


@pytest.mark.asyncio
async def test_unload_idle_models(monkeypatch):
    # 200 → early return, no verify
    post = AsyncMock(return_value=SimpleNamespace(status_code=200))
    get = AsyncMock()
    client = SimpleNamespace(is_closed=False, post=post, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    await D._unload_idle_models()
    get.assert_not_awaited()

    # non-200 → verifies via /api/ps
    post = AsyncMock(return_value=SimpleNamespace(status_code=500))
    get = AsyncMock(return_value=SimpleNamespace(
        status_code=200, json=lambda: {"models": []}))
    client = SimpleNamespace(is_closed=False, post=post, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    monkeypatch.setattr(D.asyncio, "sleep", AsyncMock())
    await D._unload_idle_models()
    get.assert_awaited_once()

    # transport error → also verifies
    import httpx
    post = AsyncMock(side_effect=httpx.TransportError("dropped"))
    get.reset_mock()
    await D._unload_idle_models()
    get.assert_awaited_once()

    # client unavailable → silent return
    monkeypatch.setattr(D, "_get_client", AsyncMock(
        side_effect=ConnectionError("down")))
    await D._unload_idle_models()


@pytest.mark.asyncio
async def test_free_gpu_memory(monkeypatch):
    unload = AsyncMock()
    monkeypatch.setattr(D, "_unload_idle_models", unload)

    # no COMFYUI_URL → unload only
    monkeypatch.setattr(D.settings, "COMFYUI_URL", "", raising=False)
    await D._free_gpu_memory()
    unload.assert_awaited_once()

    # comfy busy → skip /free
    monkeypatch.setattr(D.settings, "COMFYUI_URL", "http://comfy",
                        raising=False)
    post = AsyncMock()
    get = AsyncMock(return_value=SimpleNamespace(
        status_code=200, json=lambda: {"queue_running": [[1]]}))
    client = SimpleNamespace(is_closed=False, post=post, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    await D._free_gpu_memory()
    post.assert_not_awaited()

    # comfy idle → /free posted
    get = AsyncMock(return_value=SimpleNamespace(
        status_code=200, json=lambda: {"queue_running": []}))
    post = AsyncMock(return_value=SimpleNamespace(status_code=200))
    client = SimpleNamespace(is_closed=False, post=post, get=get)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    await D._free_gpu_memory()
    assert post.await_args.kwargs["json"]["unload_models"] is True


@pytest.mark.asyncio
async def test_warmup_lock(monkeypatch):
    # acquired → token stored, True
    r = SimpleNamespace(set=AsyncMock(return_value=True), aclose=AsyncMock())
    monkeypatch.setattr(D, "_redis_client", lambda: r)
    assert await D._try_acquire_warmup_lock() is True
    assert D._state.warmup_lock_token

    # not acquired → False
    r.set = AsyncMock(return_value=None)
    assert await D._try_acquire_warmup_lock() is False

    # redis down → degrade to True
    monkeypatch.setattr(D, "_redis_client",
                        Mock(side_effect=ConnectionError("down")))
    assert await D._try_acquire_warmup_lock() is True

    # release — holds token → eval'd
    D._state.warmup_lock_token = "tok"
    r2 = SimpleNamespace(eval=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(D, "_redis_client", lambda: r2)
    await D._release_warmup_lock()
    r2.eval.assert_awaited_once()
    assert D._state.warmup_lock_token is None

    # no token → no-op
    await D._release_warmup_lock()


@pytest.mark.asyncio
async def test_warmup_models_gates(monkeypatch):
    D._state.warmup_done = False
    body = AsyncMock()
    monkeypatch.setattr(D, "_warm_models_locked", body)
    monkeypatch.setattr(D, "_release_warmup_lock", AsyncMock())

    # offline → body not run
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(
        return_value=False))
    D._state.warmup_done = False
    await D.warmup_models()
    body.assert_not_awaited()

    # another worker holds lock → skip
    monkeypatch.setattr(D, "_check_dmr_health", AsyncMock(
        return_value=True))
    monkeypatch.setattr(D, "_try_acquire_warmup_lock", AsyncMock(
        return_value=False))
    D._state.warmup_done = False
    await D.warmup_models()
    body.assert_not_awaited()

    # force → runs body + releases lock
    D._state.warmup_done = False
    rel = D._release_warmup_lock
    await D.warmup_models(force=True)
    body.assert_awaited_once()
    rel.assert_awaited_once()

    # done → immediate return
    await D.warmup_models(force=True)
    assert body.await_count == 1


@pytest.mark.asyncio
async def test_warm_models_locked(monkeypatch):
    monkeypatch.setattr(D, "apply_best_practice_configs", AsyncMock())
    monkeypatch.setattr(D.settings, "DMR_MID_MODEL", "mid-4b",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_TINY_MODEL", "tiny",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_TEXT_MODEL", "qwen3:8b",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_VISION_MODEL", "qwen3-vl",
                        raising=False)
    warmed: list[str | None] = []

    async def _chat(prompt, *, model_override=None, **_kw):
        warmed.append(model_override)
        return {}

    monkeypatch.setattr(D, "_call_dmr_chat_internal", _chat)

    # VRAM unmeasurable → mid only
    monkeypatch.setattr(D, "_get_vram_info", AsyncMock(return_value=None))
    await D._warm_models_locked()
    assert warmed == ["mid-4b"]

    # ample VRAM → mid + llama3.2 + 8B (+vision if budget allows)
    warmed.clear()
    monkeypatch.setattr(D, "_get_vram_info", AsyncMock(
        return_value={"free": 20_000, "used": 0, "total": 20_000}))
    await D._warm_models_locked()
    assert "mid-4b" in warmed and "ai/llama3.2" in warmed
    assert "qwen3:8b" in warmed  # budget allowed it
    assert "qwen3-vl" in warmed

    # tight VRAM → mid + llama3.2, no 8B/vision
    warmed.clear()
    monkeypatch.setattr(D, "_get_vram_info", AsyncMock(
        return_value={"free": 5_500, "used": 0, "total": 8_000}))
    await D._warm_models_locked()
    assert "mid-4b" in warmed and "ai/llama3.2" in warmed
    assert "qwen3:8b" not in warmed and "qwen3-vl" not in warmed


@pytest.mark.asyncio
async def test_apply_best_practice_configs(monkeypatch):
    post = AsyncMock(return_value=SimpleNamespace(status_code=200))
    monkeypatch.setattr(D, "_post_configure", post)
    monkeypatch.setattr(D, "_get_model_overrides", AsyncMock(
        return_value={}))
    await D.apply_best_practice_configs()
    assert post.await_count == len(D._BEST_PRACTICE_CONFIGS)


# ── vllm + vision call paths ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_call_dmr_vllm_chat(monkeypatch):
    import app.services.gpu_arbiter as GA
    monkeypatch.setattr(GA, "dmr_slot", lambda **_kw: _Slot())

    # not configured → ConnectionError
    monkeypatch.setattr(D.settings, "DMR_VLLM_URL", "", raising=False)
    with pytest.raises(ConnectionError, match="vLLM"):
        await D.call_dmr_vllm_chat("hi")

    # happy path
    monkeypatch.setattr(D.settings, "DMR_VLLM_URL", "http://vllm/v1",
                        raising=False)
    post = AsyncMock(return_value=SimpleNamespace(
        status_code=200,
        json=lambda: {"choices": [{"message": {
            "content": "<think>t</think>answer"}}]}))
    client = SimpleNamespace(is_closed=False, post=post)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    out = await D.call_dmr_vllm_chat("hi", system="sys")
    assert out == {"text": "answer", "backend": "vllm",
                   "model": "docker.io/ai/smollm2-vllm:latest"}
    payload = post.await_args.kwargs["json"]
    assert payload["messages"][0]["role"] == "system"

    # error status → ConnectionError
    post = AsyncMock(return_value=SimpleNamespace(
        status_code=502, text="bad"))
    client.post = post
    with pytest.raises(ConnectionError, match="502"):
        await D.call_dmr_vllm_chat("hi")


@pytest.mark.asyncio
async def test_call_dmr_vision(monkeypatch):
    import app.services.gpu_arbiter as GA
    monkeypatch.setattr(GA, "await_media_idle", AsyncMock())
    monkeypatch.setattr(GA, "dmr_slot", lambda **_kw: _Slot())
    monkeypatch.setattr(D, "_has_vram_for_model", AsyncMock(
        return_value=True))
    monkeypatch.setattr(D, "_ensure_model_configured", AsyncMock())
    monkeypatch.setattr(D.settings, "DMR_URL", "http://dmr/v1",
                        raising=False)
    monkeypatch.setattr(D.settings, "DMR_VISION_MODEL", "qwen3-vl",
                        raising=False)

    # happy path — text content returned
    post = AsyncMock(return_value=SimpleNamespace(
        status_code=200,
        json=lambda: {"choices": [{"message": {"content": "yes"}}]}))
    client = SimpleNamespace(is_closed=False, post=post)
    monkeypatch.setattr(D, "_get_client", AsyncMock(return_value=client))
    out = await D.call_dmr_vision("aW1n", "what is this")
    assert out == "yes"
    payload = post.await_args.kwargs["json"]
    assert payload["model"] == "qwen3-vl"
    # raw b64 wrapped in a data URI
    img = payload["messages"][0]["content"][1]["image_url"]["url"]
    assert img.startswith("data:image/")

    # reasoning_content fallback when content empty
    post = AsyncMock(return_value=SimpleNamespace(
        status_code=200,
        json=lambda: {"choices": [{"message": {
            "content": "", "reasoning_content": "thought"}}]}))
    client.post = post
    out = await D.call_dmr_vision("aW1n", "q")
    assert out == "thought"

    # 5xx then success — heal + retry inside one slot
    ok = SimpleNamespace(status_code=200, json=lambda: {
        "choices": [{"message": {"content": "healed"}}]})
    post = AsyncMock(side_effect=[
        SimpleNamespace(status_code=500), ok])
    client.post = post
    heal = AsyncMock()
    monkeypatch.setattr(D, "_free_gpu_memory", heal)
    out = await D.call_dmr_vision("aW1n", "q")
    assert out == "healed"
    # CPU-pinned vision model → no GPU heal, but config re-push happened
    heal.assert_not_awaited()

    # persistent non-200 → None
    post = AsyncMock(return_value=SimpleNamespace(status_code=400))
    client.post = post
    assert await D.call_dmr_vision("aW1n", "q") is None

    # transport error → None (non-raising contract)
    import httpx
    post = AsyncMock(side_effect=httpx.TransportError("down"))
    client.post = post
    assert await D.call_dmr_vision("aW1n", "q") is None

"""Unit tests for DMR GPU-memory healing, HTTP model configuration, and
the single-worker warmup lock (PRs #310-#312).

Covers the failure chain that produced "unable to load runner / not enough
GPU memory (CUDA)": config-loss on unload, blind VRAM gating without
nvidia-smi, ComfyUI contention, and 4-worker warmup storms.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services import dmr


class _Resp:
    def __init__(self, status_code: int, body=None, text: str = ""):
        self.status_code = status_code
        self._body = body
        self.text = text or str(body or "")

    def json(self):
        return self._body


class _RouterClient:
    """Fake httpx.AsyncClient with per-URL response routing + call log."""

    is_closed = False

    def __init__(self):
        self.calls: list[tuple[str, str, object]] = []  # (method, url, json)
        self.post_responses: dict[str, list[_Resp]] = {}
        self.get_responses: dict[str, _Resp] = {}

    def queue_post(self, url_part: str, *resps: _Resp) -> None:
        self.post_responses.setdefault(url_part, []).extend(resps)

    def set_get(self, url_part: str, resp: _Resp) -> None:
        self.get_responses[url_part] = resp

    async def post(self, url, json=None, **_kw):
        self.calls.append(("post", url, json))
        for part, resps in self.post_responses.items():
            if part in url:
                return resps.pop(0) if resps else _Resp(200, {})
        return _Resp(200, {})

    async def get(self, url, **_kw):
        self.calls.append(("get", url, None))
        for part, resp in self.get_responses.items():
            if part in url:
                return resp
        return _Resp(404, {})


@pytest.fixture
def fake_client(monkeypatch):
    client = _RouterClient()
    monkeypatch.setattr(dmr, "_get_client", AsyncMock(return_value=client))
    return client


@pytest.fixture(autouse=True)
def _clean_state():
    dmr._state.configured = {}
    dmr._state.vram = {}
    dmr._state.vram_check = 0.0
    yield
    dmr._state.configured = {}
    dmr._state.vram = {}
    dmr._state.vram_check = 0.0


# ── _configure_payload ───────────────────────────────────────────────────────


class TestConfigurePayload:
    def test_full_model_maps_all_keys(self):
        body = dmr._configure_payload("hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M")
        assert body["model"].endswith("Q4_K_M")
        assert body["context-size"] == 4096
        assert body["keep_alive"] == "30m"
        assert "--n-gpu-layers" in body["runtime-flags"]

    def test_think_maps_to_llamacpp_reasoning_budget(self):
        body = dmr._configure_payload("ai/qwen3:8b-q4_K_M")
        assert body["llamacpp"] == {"reasoning-budget": -1}
        assert body["context-size"] == 6144

    def test_embedding_mode_mapped(self):
        body = dmr._configure_payload("ai/qwen3-embedding")
        assert body["mode"] == "embedding"

    def test_unlisted_model_returns_none(self):
        assert dmr._configure_payload("ai/llama-70b") is None

    def test_short_ref_suffix_matches(self):
        body = dmr._configure_payload("qwen3-vl")
        assert body is not None and body["context-size"] == 4096


# ── _ensure_model_configured ─────────────────────────────────────────────────


class TestEnsureModelConfigured:
    @pytest.mark.asyncio
    async def test_posts_configure_and_caches(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr.settings, "DMR_URL", "http://dmr/engines/llama.cpp/v1")
        await dmr._ensure_model_configured("ai/smollm3")
        posts = [c for c in fake_client.calls if c[0] == "post" and "_configure" in c[1]]
        assert len(posts) == 1
        assert posts[0][2]["model"] == "ai/smollm3"
        assert posts[0][2]["context-size"] == 2048

    @pytest.mark.asyncio
    async def test_ttl_skips_second_call(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr.settings, "DMR_URL", "http://dmr/engines/llama.cpp/v1")
        await dmr._ensure_model_configured("ai/smollm3")
        await dmr._ensure_model_configured("ai/smollm3")
        posts = [c for c in fake_client.calls if c[0] == "post"]
        assert len(posts) == 1

    @pytest.mark.asyncio
    async def test_force_bypasses_ttl(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr.settings, "DMR_URL", "http://dmr/engines/llama.cpp/v1")
        await dmr._ensure_model_configured("ai/smollm3")
        await dmr._ensure_model_configured("ai/smollm3", force=True)
        posts = [c for c in fake_client.calls if c[0] == "post"]
        assert len(posts) == 2

    @pytest.mark.asyncio
    async def test_unlisted_model_no_post(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr.settings, "DMR_URL", "http://dmr/engines/llama.cpp/v1")
        await dmr._ensure_model_configured("ai/unknown-70b")
        assert not fake_client.calls


# ── _free_gpu_memory ─────────────────────────────────────────────────────────


class TestFreeGpuMemory:
    @pytest.mark.asyncio
    async def test_frees_comfyui_when_queue_idle(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr, "_unload_idle_models", AsyncMock())
        monkeypatch.setattr(dmr.settings, "COMFYUI_URL", "http://comfyui:8000")
        fake_client.set_get("queue", _Resp(200, {"queue_running": [], "queue_pending": []}))
        await dmr._free_gpu_memory()
        dmr._unload_idle_models.assert_awaited_once()
        frees = [c for c in fake_client.calls if "/free" in c[1]]
        assert len(frees) == 1
        assert frees[0][2] == {"unload_models": True, "free_memory": True}

    @pytest.mark.asyncio
    async def test_skips_free_when_comfyui_rendering(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr, "_unload_idle_models", AsyncMock())
        monkeypatch.setattr(dmr.settings, "COMFYUI_URL", "http://comfyui:8000")
        fake_client.set_get("queue", _Resp(200, {"queue_running": [{"job": 1}]}))
        await dmr._free_gpu_memory()
        assert not [c for c in fake_client.calls if "/free" in c[1]]

    @pytest.mark.asyncio
    async def test_comfyui_down_does_not_raise(self, monkeypatch):
        monkeypatch.setattr(dmr, "_unload_idle_models", AsyncMock())
        monkeypatch.setattr(dmr.settings, "COMFYUI_URL", "http://comfyui:8000")
        broken = MagicMock()
        broken.get = AsyncMock(side_effect=ConnectionError("down"))
        monkeypatch.setattr(dmr, "_get_client", AsyncMock(return_value=broken))
        await dmr._free_gpu_memory()  # must not raise


# ── VRAM oracle fallback ─────────────────────────────────────────────────────


class TestVramOracle:
    @pytest.mark.asyncio
    async def test_comfyui_fallback_when_no_nvidia_smi(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr, "_nvidia_smi_vram", lambda: None)
        monkeypatch.setattr(dmr.settings, "COMFYUI_URL", "http://comfyui:8000")
        mib = 1024 * 1024
        fake_client.set_get("system_stats", _Resp(200, {
            "devices": [{"type": "cuda", "vram_total": 8192 * mib, "vram_free": 6144 * mib}],
        }))
        vram = await dmr._get_vram_info()
        assert vram == {"used": 2048, "free": 6144, "total": 8192}

    @pytest.mark.asyncio
    async def test_no_sources_returns_none(self, monkeypatch):
        monkeypatch.setattr(dmr, "_nvidia_smi_vram", lambda: None)
        monkeypatch.setattr(dmr.settings, "COMFYUI_URL", "")
        assert await dmr._get_vram_info() is None


# ── OOM heal retry in _call_dmr_chat_internal ────────────────────────────────


class TestOomHealRetry:
    @pytest.mark.asyncio
    async def test_runner_load_500_triggers_heal_and_retry(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr.settings, "DMR_URL", "http://dmr/engines/llama.cpp/v1")
        monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
        monkeypatch.setattr(dmr, "_has_vram_for_model", AsyncMock(return_value=True))

        heal = AsyncMock()
        ensure = AsyncMock()
        monkeypatch.setattr(dmr, "_free_gpu_memory", heal)
        monkeypatch.setattr(dmr, "_ensure_model_configured", ensure)

        fake_client.queue_post(
            "chat/completions",
            _Resp(500, text="unable to load runner: not enough GPU memory to load the model (CUDA)"),
            _Resp(200, {"choices": [{"message": {"content": "recovered"}}]}),
        )

        result = await dmr._call_dmr_chat_internal(
            "hi", model_override="ai/smollm3", max_tokens=5, _skip_health_check=False,
        )
        assert result == {"text": "recovered"}
        heal.assert_awaited_once()
        # normal ensure + forced re-configure on the heal path
        assert ensure.await_count == 2
        assert ensure.await_args_list[1].kwargs.get("force") is True
        chat_posts = [c for c in fake_client.calls if "chat/completions" in c[1]]
        assert len(chat_posts) == 2

    @pytest.mark.asyncio
    async def test_non_oom_500_does_not_heal(self, fake_client, monkeypatch):
        monkeypatch.setattr(dmr.settings, "DMR_URL", "http://dmr/engines/llama.cpp/v1")
        monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
        monkeypatch.setattr(dmr, "_has_vram_for_model", AsyncMock(return_value=True))
        heal = AsyncMock()
        monkeypatch.setattr(dmr, "_free_gpu_memory", heal)
        monkeypatch.setattr(dmr, "_ensure_model_configured", AsyncMock())
        monkeypatch.setattr(dmr, "_dmr_cli_run", lambda *a, **kw: None)

        fake_client.queue_post("chat/completions", _Resp(500, text="internal server error"))
        fake_client.queue_post("chat/completions", _Resp(500, text="internal server error"))

        with pytest.raises(ConnectionError):
            await dmr._call_dmr_chat_internal("hi", model_override="ai/smollm3")
        heal.assert_not_awaited()


# ── Single-worker warmup lock ────────────────────────────────────────────────


class TestWarmupLock:
    @pytest.mark.asyncio
    async def test_lock_held_skips_warmup(self, monkeypatch):
        dmr._state.warmup_done = False
        monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
        monkeypatch.setattr(dmr, "_try_acquire_warmup_lock", AsyncMock(return_value=False))
        spy = AsyncMock()
        monkeypatch.setattr(dmr, "_call_dmr_chat_internal", spy)
        monkeypatch.setattr(dmr, "apply_best_practice_configs", AsyncMock())
        try:
            await dmr.warmup_models()
            spy.assert_not_called()
        finally:
            dmr._state.warmup_done = False

    @pytest.mark.asyncio
    async def test_force_bypasses_lock(self, monkeypatch):
        dmr._state.warmup_done = False
        monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
        lock = AsyncMock(return_value=False)
        monkeypatch.setattr(dmr, "_try_acquire_warmup_lock", lock)
        monkeypatch.setattr(dmr, "apply_best_practice_configs", AsyncMock())
        monkeypatch.setattr(dmr, "_get_vram_info", AsyncMock(return_value=None))
        spy = AsyncMock(return_value={"text": "ok"})
        monkeypatch.setattr(dmr, "_call_dmr_chat_internal", spy)
        try:
            await dmr.warmup_models(force=True)
            lock.assert_not_awaited()
            assert spy.await_count >= 1
        finally:
            dmr._state.warmup_done = False


# ── Warmup VRAM budgeting ────────────────────────────────────────────────────


class TestKeepWarm:
    """Periodic keep_alive refresh for the warm tier (mid 4B + llama3.2)."""

    def _patch(self, monkeypatch, *, busy=False, running=(), vram_ok=True):
        monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
        import app.services.gpu_arbiter as arbiter
        monkeypatch.setattr(arbiter, "media_gpu_busy", AsyncMock(return_value=busy))
        monkeypatch.setattr(dmr, "_running_dmr_models", AsyncMock(return_value=set(running)))
        monkeypatch.setattr(dmr, "_has_vram_for_model", AsyncMock(return_value=vram_ok))
        spy = AsyncMock(return_value={"text": "ok"})
        monkeypatch.setattr(dmr, "_call_dmr_chat_internal", spy)
        return spy

    @pytest.mark.asyncio
    async def test_pings_warm_tier(self, monkeypatch):
        spy = self._patch(monkeypatch)
        result = await dmr.keep_warm_models()
        models = [c.kwargs["model_override"] for c in spy.await_args_list]
        assert models == [dmr.settings.DMR_MID_MODEL, "ai/llama3.2"]
        assert result["warmed"] == models

    @pytest.mark.asyncio
    async def test_skips_when_media_job_owns_gpu(self, monkeypatch):
        spy = self._patch(monkeypatch, busy=True)
        result = await dmr.keep_warm_models()
        spy.assert_not_awaited()
        assert result == {"warmed": [], "skipped": [], "reason": "media_job"}

    @pytest.mark.asyncio
    async def test_cold_model_skipped_when_vram_tight(self, monkeypatch):
        # llama3.2 unloaded + no room to reload → skipped; the resident mid
        # model still gets its keep_alive refresh.
        spy = self._patch(
            monkeypatch,
            running={"huggingface.co/unsloth/qwen3-4b-instruct-2507-gguf:q4_k_m"},
            vram_ok=False,
        )
        result = await dmr.keep_warm_models()
        models = [c.kwargs["model_override"] for c in spy.await_args_list]
        assert models == [dmr.settings.DMR_MID_MODEL]
        assert result["skipped"] == ["ai/llama3.2"]

    @pytest.mark.asyncio
    async def test_resident_model_pings_despite_tight_vram(self, monkeypatch):
        # A loaded model costs ~0 extra VRAM to ping — always refresh it.
        self._patch(
            monkeypatch,
            running={
                "huggingface.co/unsloth/qwen3-4b-instruct-2507-gguf:q4_k_m",
                "docker.io/ai/llama3.2:latest",
            },
            vram_ok=False,
        )
        result = await dmr.keep_warm_models()
        assert result["warmed"] == [dmr.settings.DMR_MID_MODEL, "ai/llama3.2"]
        assert result["skipped"] == []


class TestWarmupBudget:
    @pytest.mark.asyncio
    async def test_skips_8b_when_budget_insufficient(self, monkeypatch):
        """4GB free after mid model → 8B (est 5.5GB+1GB headroom) skipped."""
        dmr._state.warmup_done = False
        monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
        monkeypatch.setattr(dmr, "_try_acquire_warmup_lock", AsyncMock(return_value=True))
        monkeypatch.setattr(dmr, "apply_best_practice_configs", AsyncMock())
        monkeypatch.setattr(
            dmr, "_get_vram_info",
            AsyncMock(return_value={"used": 4192, "free": 6700, "total": 8192}),
        )
        monkeypatch.setattr(dmr.settings, "DMR_MID_MODEL", "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M")
        monkeypatch.setattr(dmr.settings, "DMR_TEXT_MODEL", "ai/qwen3:8b-q4_K_M")
        warmed: list[str] = []

        async def _spy(prompt, *, model_override=None, **kw):
            warmed.append(model_override or "")
            return {"text": "ok"}

        monkeypatch.setattr(dmr, "_call_dmr_chat_internal", _spy)
        try:
            await dmr.warmup_models()
            # 6700 free - 2700 mid = 4000 budget → llama3.2 (2200) fits and
            # warms first, leaving 1800 < 5500+1000 → 8B skipped, vision
            # (5000+1000) also skipped.
            assert warmed == ["hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M", "ai/llama3.2"]
        finally:
            dmr._state.warmup_done = False

"""Regression tests for the DMR follow-ups to #312, #314 and #315.

* #312 — the Redis warmup lock is released after warmup (and on shutdown),
  and unmeasurable VRAM is treated as "warm only the mid/small model",
  never as unlimited.
* #314 — admin keep-alive / speculative-decoding overrides are persisted
  per model and merged into every _configure payload, so the ~60s
  canonical re-push no longer wipes them and re-applying is never a no-op.
* #315 — WebP/AVIF -> PNG transcoding flattens transparency onto white
  and runs off the event loop.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import AsyncMock

import pytest
from PIL import Image

from app.services import dmr

MID = "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"
TEXT = "ai/qwen3:8b-q4_K_M"


class _Resp:
    def __init__(self, status_code: int = 200):
        self.status_code = status_code

    def json(self):
        return {}


class _Client:
    is_closed = False

    def __init__(self, status: int = 200):
        self.status = status
        self.posts: list[tuple[str, dict]] = []

    async def post(self, url, json=None, **_kw):
        self.posts.append((url, json))
        return _Resp(self.status)

    def configure_bodies(self) -> list[dict]:
        return [b for u, b in self.posts if u.endswith("/engines/_configure")]


class _FakeRedis:
    """Minimal async Redis double shared across "workers"."""

    def __init__(self):
        self.kv: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self.kv:
            return None
        self.kv[key] = value
        return True

    async def eval(self, _script, _numkeys, key, token):
        if self.kv.get(key) == token:
            del self.kv[key]
            return 1
        return 0

    async def hget(self, name, field):
        return self.hashes.get(name, {}).get(field)

    async def hset(self, name, field, value):
        self.hashes.setdefault(name, {})[field] = value

    async def hdel(self, name, field):
        self.hashes.get(name, {}).pop(field, None)

    async def aclose(self):
        pass


@pytest.fixture
def fake_redis(monkeypatch):
    r = _FakeRedis()
    monkeypatch.setattr(dmr, "_redis_client", lambda: r)
    return r


@pytest.fixture
def client(monkeypatch):
    c = _Client()
    monkeypatch.setattr(dmr, "_get_client", AsyncMock(return_value=c))
    return c


@pytest.fixture(autouse=True)
def _clean_state():
    def _reset():
        dmr._state.configured = {}
        dmr._state.overrides = {}
        dmr._state.override_store_down_until = 0.0
        dmr._state.warmup_lock_token = None
        dmr._state.warmup_done = False
        dmr._state.vram = {}
        dmr._state.vram_check = 0.0

    _reset()
    yield
    _reset()


# ── #312: warmup lock release ────────────────────────────────────────────────


def _patch_warmup(monkeypatch, vram):
    monkeypatch.setattr(dmr, "_check_dmr_health", AsyncMock(return_value=True))
    monkeypatch.setattr(dmr, "apply_best_practice_configs", AsyncMock())
    monkeypatch.setattr(dmr, "_get_vram_info", AsyncMock(return_value=vram))
    monkeypatch.setattr(dmr.settings, "DMR_MID_MODEL", MID)
    monkeypatch.setattr(dmr.settings, "DMR_TEXT_MODEL", TEXT)
    warmed: list[str] = []

    async def _spy(prompt, *, model_override=None, **kw):
        warmed.append(model_override or "")
        return {"text": "ok"}

    monkeypatch.setattr(dmr, "_call_dmr_chat_internal", _spy)
    return warmed


class TestWarmupLockRelease:
    @pytest.mark.asyncio
    async def test_lock_released_after_successful_warmup(self, monkeypatch, fake_redis):
        _patch_warmup(monkeypatch, {"used": 0, "free": 8000, "total": 8192})
        await dmr.warmup_models()
        assert dmr._WARMUP_LOCK_KEY not in fake_redis.kv
        assert dmr._state.warmup_lock_token is None

    @pytest.mark.asyncio
    async def test_lock_released_when_warm_loop_raises(self, monkeypatch, fake_redis):
        _patch_warmup(monkeypatch, {"used": 0, "free": 8000, "total": 8192})
        monkeypatch.setattr(dmr, "apply_best_practice_configs", AsyncMock(side_effect=RuntimeError))
        with pytest.raises(RuntimeError):
            await dmr.warmup_models()
        assert dmr._WARMUP_LOCK_KEY not in fake_redis.kv

    @pytest.mark.asyncio
    async def test_restart_after_release_can_warm_again(self, monkeypatch, fake_redis):
        warmed = _patch_warmup(monkeypatch, {"used": 0, "free": 8000, "total": 8192})
        await dmr.warmup_models()
        first = len(warmed)
        dmr.reset_warmup()  # simulates a fresh process inside the TTL window
        await dmr.warmup_models()
        assert first >= 1 and len(warmed) == 2 * first

    @pytest.mark.asyncio
    async def test_release_does_not_delete_foreign_lock(self, fake_redis):
        fake_redis.kv[dmr._WARMUP_LOCK_KEY] = "other-worker"
        dmr._state.warmup_lock_token = "mine"
        await dmr._release_warmup_lock()
        assert fake_redis.kv[dmr._WARMUP_LOCK_KEY] == "other-worker"

    @pytest.mark.asyncio
    async def test_shutdown_releases_held_lock(self, monkeypatch, fake_redis):
        assert await dmr._try_acquire_warmup_lock() is True
        assert dmr._WARMUP_LOCK_KEY in fake_redis.kv
        monkeypatch.setattr(dmr, "close_client", AsyncMock())
        await dmr.on_shutdown()
        assert dmr._WARMUP_LOCK_KEY not in fake_redis.kv

    @pytest.mark.asyncio
    async def test_second_worker_skips_while_lock_held(self, monkeypatch, fake_redis):
        warmed = _patch_warmup(monkeypatch, {"used": 0, "free": 8000, "total": 8192})
        fake_redis.kv[dmr._WARMUP_LOCK_KEY] = "other-worker"
        await dmr.warmup_models()
        assert warmed == []
        assert fake_redis.kv[dmr._WARMUP_LOCK_KEY] == "other-worker"


class TestWarmupUnknownVram:
    @pytest.mark.asyncio
    async def test_unmeasurable_vram_warms_only_mid(self, monkeypatch, fake_redis):
        warmed = _patch_warmup(monkeypatch, None)
        await dmr.warmup_models()
        assert warmed == [MID]

    @pytest.mark.asyncio
    async def test_unmeasurable_vram_falls_back_to_tiny(self, monkeypatch, fake_redis):
        warmed = _patch_warmup(monkeypatch, None)
        monkeypatch.setattr(dmr.settings, "DMR_MID_MODEL", "")
        monkeypatch.setattr(dmr.settings, "DMR_TINY_MODEL", "ai/smollm3")
        await dmr.warmup_models()
        assert warmed == ["ai/smollm3"]

    @pytest.mark.asyncio
    async def test_ample_vram_still_warms_text_model(self, monkeypatch, fake_redis):
        warmed = _patch_warmup(monkeypatch, {"used": 0, "free": 10000, "total": 12288})
        await dmr.warmup_models()
        assert warmed[:2] == [MID, TEXT]


# ── #314: persisted admin overrides ──────────────────────────────────────────


class TestOverridePersistence:
    @pytest.mark.asyncio
    async def test_keep_alive_survives_periodic_repush(self, client, fake_redis):
        await dmr.configure_keep_alive(TEXT, "1h")
        assert client.configure_bodies()[-1]["keep_alive"] == "1h"
        # TTL expiry → canonical re-push must still carry the override
        dmr._state.configured = {}
        await dmr._ensure_model_configured(TEXT)
        body = client.configure_bodies()[-1]
        assert body["keep_alive"] == "1h"
        assert body["context-size"] == 6144  # canonical config intact

    @pytest.mark.asyncio
    async def test_override_reaches_other_workers(self, client, fake_redis):
        await dmr.configure_keep_alive(TEXT, "1h")
        dmr._state.overrides = {}  # a different worker: empty local mirror
        dmr._state.configured = {}
        await dmr._ensure_model_configured(TEXT, force=True)
        assert client.configure_bodies()[-1]["keep_alive"] == "1h"

    @pytest.mark.asyncio
    async def test_reapply_same_value_is_not_a_noop(self, client, fake_redis):
        await dmr.configure_keep_alive(TEXT, "1h")
        await dmr.configure_keep_alive(TEXT, "1h")
        assert len(client.configure_bodies()) == 2

    @pytest.mark.asyncio
    async def test_speculative_and_keep_alive_both_merged(self, client, fake_redis):
        await dmr.configure_speculative_decoding(TEXT, draft_model="hf.co/x/draft")
        await dmr.configure_keep_alive(TEXT, "-1")
        dmr._state.configured = {}
        await dmr._ensure_model_configured(TEXT)
        body = client.configure_bodies()[-1]
        assert body["speculative"] == {"draft_model": "hf.co/x/draft"}
        assert body["keep_alive"] == "-1"
        assert body["runtime-flags"]  # canonical flags kept

    @pytest.mark.asyncio
    async def test_speculative_reapply_is_not_a_noop(self, client, fake_redis):
        await dmr.configure_speculative_decoding(TEXT, draft_model="hf.co/x/draft")
        await dmr.configure_speculative_decoding(TEXT, draft_model="hf.co/x/draft")
        assert len(client.configure_bodies()) == 2

    @pytest.mark.asyncio
    async def test_clear_overrides_restores_canonical(self, client, fake_redis):
        await dmr.configure_keep_alive(TEXT, "1h")
        await dmr.configure_speculative_decoding(TEXT, draft_model="hf.co/x/draft")
        await dmr.clear_model_overrides(TEXT)
        body = client.configure_bodies()[-1]
        assert body["keep_alive"] == "5m"
        assert "speculative" not in body
        assert fake_redis.hashes.get(dmr._OVERRIDES_KEY, {}).get(TEXT) is None

    @pytest.mark.asyncio
    async def test_unlisted_model_override_is_repushed(self, client, fake_redis):
        await dmr.configure_keep_alive("ai/unlisted", "10m")
        dmr._state.configured = {}
        await dmr._ensure_model_configured("ai/unlisted")
        assert client.configure_bodies()[-1] == {"model": "ai/unlisted", "keep_alive": "10m"}

    @pytest.mark.asyncio
    async def test_unlisted_model_without_override_not_pushed(self, client, fake_redis):
        await dmr._ensure_model_configured("ai/unlisted")
        assert client.configure_bodies() == []

    @pytest.mark.asyncio
    async def test_redis_down_falls_back_to_local_mirror(self, client, monkeypatch):
        def _boom():
            raise ConnectionError("redis down")

        monkeypatch.setattr(dmr, "_redis_client", _boom)
        await dmr.configure_keep_alive(TEXT, "1h")
        dmr._state.configured = {}
        await dmr._ensure_model_configured(TEXT)
        assert client.configure_bodies()[-1]["keep_alive"] == "1h"

    def test_merge_overrides_does_not_mutate_canonical(self):
        base = dmr._configure_payload(TEXT)
        merged = dmr._merge_overrides(base, {"keep_alive": "1h"})
        assert merged["keep_alive"] == "1h"
        assert dmr._configure_payload(TEXT)["keep_alive"] == "5m"


# ── #315: transparency-preserving transcode ──────────────────────────────────


def _data_uri(img: Image.Image, fmt: str) -> str:
    buf = io.BytesIO()
    img.save(buf, fmt)
    return f"data:image/{fmt.lower()};base64," + base64.b64encode(buf.getvalue()).decode()


def _decode(uri: str) -> Image.Image:
    assert uri.startswith("data:image/png;base64,")
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))


def _transparent_with_red_square() -> Image.Image:
    img = Image.new("RGBA", (32, 32), (0, 0, 0, 0))  # fully transparent black
    for x in range(8, 24):
        for y in range(8, 24):
            img.putpixel((x, y), (255, 0, 0, 255))
    return img


class TestTranscodeTransparency:
    def test_webp_alpha_flattened_onto_white(self):
        out = _decode(dmr._to_vision_safe_uri(_data_uri(_transparent_with_red_square(), "WEBP")))
        assert out.mode == "RGB"
        assert out.getpixel((0, 0)) == (255, 255, 255)  # was black via convert("RGB")
        r, g, b = out.getpixel((16, 16))
        assert r > 240 and g < 15 and b < 15

    def test_webp_semitransparent_blends_with_white(self):
        img = Image.new("RGBA", (8, 8), (0, 0, 0, 128))
        out = _decode(dmr._to_vision_safe_uri(_data_uri(img, "WEBP")))
        r, g, b = out.getpixel((4, 4))
        assert 110 <= r <= 145 and r == g == b

    def test_avif_alpha_flattened_onto_white(self):
        try:
            import pillow_avif  # noqa: F401
        except ImportError:
            pass
        if "AVIF" not in Image.SAVE:
            pytest.skip("Pillow built without AVIF support")
        out = _decode(dmr._to_vision_safe_uri(_data_uri(_transparent_with_red_square(), "AVIF")))
        r, g, b = out.getpixel((1, 1))
        assert min(r, g, b) > 240

    def test_opaque_webp_still_transcoded(self):
        img = Image.new("RGB", (8, 8), (10, 200, 30))
        out = _decode(dmr._to_vision_safe_uri(_data_uri(img, "WEBP")))
        r, g, b = out.getpixel((4, 4))
        assert g > 180 and r < 40

    def test_palette_transparency_flattened(self):
        p = Image.new("P", (8, 8), 0)
        p.putpalette([0, 0, 0, 255, 0, 0] + [0] * (256 * 3 - 6))
        p.putpixel((4, 4), 1)
        p.info["transparency"] = 0  # palette index 0 (black) is transparent
        out = dmr._flatten_alpha(p)
        assert out.getpixel((4, 4)) == (255, 0, 0)
        assert out.mode == "RGB" and out.getpixel((0, 0)) == (255, 255, 255)

    def test_safe_formats_pass_through(self):
        uri = _data_uri(Image.new("RGBA", (4, 4), (0, 0, 0, 0)), "PNG")
        assert dmr._to_vision_safe_uri(uri) == uri

    def test_undecodable_payload_sent_as_is(self):
        uri = "data:image/webp;base64," + base64.b64encode(b"not an image").decode()
        assert dmr._to_vision_safe_uri(uri) == uri

    @pytest.mark.asyncio
    async def test_vision_transcodes_off_event_loop(self, monkeypatch, client):
        calls: list = []

        async def _to_thread(fn, *args, **kw):
            calls.append(fn)
            return fn(*args, **kw)

        monkeypatch.setattr(dmr.asyncio, "to_thread", _to_thread)
        monkeypatch.setattr(dmr, "_has_vram_for_model", AsyncMock(return_value=True))
        monkeypatch.setattr(dmr, "_ensure_model_configured", AsyncMock())
        await dmr.call_dmr_vision(_data_uri(_transparent_with_red_square(), "WEBP"), "describe")
        assert dmr._to_vision_safe_uri in calls

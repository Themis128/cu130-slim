"""Unified Docker Model Runner (DMR) client.

Consolidates all DMR interactions across the backend into a single module with:
  1. CLI fallback when HTTP API is unreachable (WSL2/Docker Desktop)
  2. Pre-flight health check with caching (skip DMR fast when offline)
  3. Connection pooling (shared httpx.AsyncClient with keep-alive)
  4. Retry on cold-start timeout (1 retry with backoff)
  5. Model warm-up on startup (pre-load models into VRAM)
  6. Per-request model routing (short→smollm3, complex→qwen3)
  7. Streaming support for long generations
  8. Shared vision helper (replaces duplicates in image_enhance.py & media_ai.py)
  9. Tool calling support (OpenAI function-calling format)
 10. Keep-alive configuration (POST /inference/_configure)
 11. VRAM-aware routing (check nvidia-smi before loading large models)
 12. Speculative decoding configuration (draft model for faster generation)
 13. Benchmark caching (cache TPS results for model selection)
 14. Request logging via DMR requests API

All public functions are async and safe to call concurrently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.log_sanitize import sanitize_log_text

logger = logging.getLogger(__name__)
settings = get_settings()


class _Mutable:
    """Tiny namespace so module caches avoid CodeQL unused-global findings."""

    __slots__ = (
        "online",
        "last_check",
        "vram",
        "vram_check",
        "warmup_done",
        "configured",
        "client",
        "client_lock",
        "client_loop",
        "sem",
        "sem_loop",
    )

    def __init__(self) -> None:
        self.online: bool | None = None
        self.last_check: float = 0.0
        self.vram: dict[str, Any] = {}
        self.vram_check: float = 0.0
        self.warmup_done: bool = False
        self.configured: dict[str, float] = {}
        self.client: httpx.AsyncClient | None = None
        self.client_lock = asyncio.Lock()
        self.client_loop: asyncio.AbstractEventLoop | None = None
        self.sem: asyncio.Semaphore | None = None
        self.sem_loop: asyncio.AbstractEventLoop | None = None


_state = _Mutable()

# ── Connection pool (improvement #3) ─────────────────────────────────────────
# Shared httpx.AsyncClient with keep-alive.  Created lazily on first use.
# The client AND its pooled connections are bound to the event loop that
# created them.  Celery prefork tasks run each invocation in a fresh
# ``asyncio.run()`` loop, so the client must be recreated when the running
# loop changes — otherwise a pooled socket from a dead loop fails instantly
# and the next call succeeds on a fresh socket (alternating health results).
async def _get_client() -> httpx.AsyncClient:
    """Return the shared httpx.AsyncClient, creating it if needed."""
    loop = asyncio.get_running_loop()
    if (
        _state.client is not None
        and not _state.client.is_closed
        and _state.client_loop is loop
    ):
        return _state.client
    # The lock binds to the first loop that contends for it — recreate it
    # alongside the client so cross-loop use can't deadlock/raise.
    if _state.client_loop is not loop:
        _state.client_lock = asyncio.Lock()
    async with _state.client_lock:
        loop = asyncio.get_running_loop()
        if (
            _state.client is None
            or _state.client.is_closed
            or _state.client_loop is not loop
        ):
            old = _state.client
            _state.client = httpx.AsyncClient(
                timeout=httpx.Timeout(300.0, connect=5.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                headers={"Content-Type": "application/json"},
            )
            _state.client_loop = loop
            if old is not None and not old.is_closed:
                try:
                    await old.aclose()
                except Exception:
                    pass  # bound to a dead loop — abandon it
    return _state.client


async def close_client() -> None:
    """Close the shared HTTP client (call on app shutdown)."""
    if _state.client is not None:
        try:
            await _state.client.aclose()
        except Exception:
            pass
        _state.client = None
        _state.client_loop = None


# ── Health check with caching (improvement #2) ───────────────────────────────
_HEALTH_CACHE_TTL = 10.0  # seconds — avoid hammering the health endpoint


async def _check_dmr_health() -> bool:
    """Quick pre-flight check: is DMR reachable?  Cached for 10 seconds."""
    now = time.monotonic()
    if _state.online is not None and (now - _state.last_check) < _HEALTH_CACHE_TTL:
        return _state.online

    url = _dmr_base_url()
    # If DMR_URL is empty, DMR is disabled
    if not settings.DMR_URL:
        _state.online = False
        _state.last_check = now
        return False

    try:
        client = await _get_client()
        resp = await client.get(
            f"{url}/engines/v1/models",
            timeout=httpx.Timeout(2.0, connect=1.0),  # fast fail
        )
        _state.online = resp.status_code == 200
    except Exception:
        _state.online = False
    _state.last_check = now
    return bool(_state.online)


def _invalidate_health_cache() -> None:
    """Force the next health check to re-probe (call after a failure)."""
    _state.last_check = 0.0


def _dmr_base_url() -> str:
    """Base URL of the DMR runner (no /engines/... suffix)."""
    base = getattr(settings, "DMR_BASE_URL", "") or ""
    if base:
        return base.rstrip("/")
    return settings.DMR_URL.replace("/engines/llama.cpp/v1", "").rstrip("/")


# ── Concurrency guard (VRAM protection on the 8GB card) ──────────────────────
def _get_semaphore() -> asyncio.Semaphore:
    """Lazy semaphore — recreated when the running loop changes (celery tasks
    each run in a fresh asyncio.run loop; a semaphore bound to a dead loop
    raises "bound to a different event loop" on contention)."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _state.sem is None or _state.sem_loop is not loop:
        _state.sem = asyncio.Semaphore(
            max(1, getattr(settings, "DMR_MAX_CONCURRENCY", 4))
        )
        _state.sem_loop = loop
    return _state.sem


# ── Thinking-model output cleanup ────────────────────────────────────────────
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)

# Runner failed to start — typically CUDA OOM while another DMR model or
# ComfyUI holds VRAM. Triggers the GPU-heal retry path in chat calls.
_RUNNER_LOAD_RE = re.compile(
    r"unable to load runner|not enough GPU memory|terminated unexpectedly|failed to fit params",
    re.IGNORECASE,
)


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> reasoning blocks (Qwen3 et al.) from output."""
    if "<think>" not in text:
        return text.strip()
    return _THINK_RE.sub("", text).strip()


async def validate_dmr_models() -> dict[str, Any]:
    """Check which configured/expected models are present in the runner store.

    Non-blocking diagnostic — used by health monitoring and admin status.
    Returns {"online": bool, "expected": [...], "present": [...], "missing": [...]}.
    """
    expected = {
        settings.DMR_TEXT_MODEL,
        settings.DMR_TINY_MODEL,
        getattr(settings, "DMR_MID_MODEL", ""),
        getattr(settings, "DMR_CHATBOT_MODEL", ""),
        settings.DMR_EMBEDDING_MODEL,
        settings.DMR_VISION_MODEL,
        "ai/qwen3:8b-q4_K_M",  # schema/JSON model
    }
    expected.discard("")
    if not settings.DMR_URL:
        return {"online": False, "expected": sorted(expected), "present": [], "missing": sorted(expected)}
    try:
        client = await _get_client()
        resp = await client.get(
            f"{_dmr_base_url()}/engines/v1/models",
            timeout=httpx.Timeout(5.0, connect=2.0),
        )
        if resp.status_code != 200:
            return {"online": False, "expected": sorted(expected), "present": [], "missing": sorted(expected)}
        data = resp.json()
        present_ids = {m.get("id", "") for m in data.get("data", [])}
        # Runner reports full refs like "docker.io/ai/llama3.2:latest" and
        # lowercases hf.co → huggingface.co — normalize before suffix-matching.
        def _norm(ref: str) -> str:
            return ref.lower().replace("hf.co/", "huggingface.co/")

        norm_ids = {_norm(p) for p in present_ids}
        present = {
            e for e in expected
            if any(p.endswith(_norm(e).split("ai/")[-1]) or _norm(e) in p for p in norm_ids)
        }
        missing = expected - present
        return {
            "online": True,
            "expected": sorted(expected),
            "present": sorted(present),
            "missing": sorted(missing),
            "available_models": sorted(present_ids),
        }
    except Exception as exc:
        logger.warning("DMR model validation failed: %s", exc)
        return {"online": False, "expected": sorted(expected), "present": [], "missing": sorted(expected), "error": str(exc)}


# ── CLI fallback (improvement #1) ─────────────────────────────────────────────


def _dmr_cli_run(model: str, prompt: str, timeout: int = 120) -> str | None:
    """Fall back to `docker model run` when the HTTP API is unreachable.

    Returns the model's text response, or None on failure.
    """
    try:
        result = subprocess.run(
            ["docker", "model", "run", model, prompt],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            return result.stdout.strip()
        logger.warning("DMR CLI fallback failed (rc=%s)", result.returncode)
    except subprocess.TimeoutExpired:
        logger.warning("DMR CLI fallback timed out (%ss)", timeout)
    except FileNotFoundError:
        logger.warning("docker CLI not found — DMR CLI fallback unavailable")
    except Exception as exc:
        logger.warning("DMR CLI fallback error (%s)", type(exc).__name__)
    return None


# ── VRAM-aware routing (improvement #11) ──────────────────────────────────────
_VRAM_CACHE_TTL = 5.0  # seconds


def _nvidia_smi_vram() -> dict[str, int] | None:
    """VRAM {used, free, total} MiB via nvidia-smi, or None when unavailable."""
    try:
        result = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.free,memory.total",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3.0,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            if len(parts) >= 3:
                return {"used": int(parts[0]), "free": int(parts[1]), "total": int(parts[2])}
    except Exception:
        pass
    return None


async def _comfyui_vram() -> dict[str, int] | None:
    """VRAM {used, free, total} MiB via ComfyUI /system_stats.

    The API container has no nvidia-smi, so without this fallback the VRAM
    gate silently allows every load. ComfyUI shares the GPU and reports real
    device memory — good enough for conservative go/no-go decisions.
    """
    base = getattr(settings, "COMFYUI_URL", "")
    if not base:
        return None
    try:
        client = await _get_client()
        resp = await client.get(
            f"{base.rstrip('/')}/system_stats",
            timeout=httpx.Timeout(2.0, connect=1.0),
        )
        if resp.status_code != 200:
            return None
        cuda = [d for d in resp.json().get("devices") or [] if d.get("type") == "cuda"]
        if not cuda:
            return None
        mib = 1024 * 1024
        free = min(int(d.get("vram_free", 0)) for d in cuda) // mib
        total = min(int(d.get("vram_total", 0)) for d in cuda) // mib
        if not total:
            return None
        return {"used": max(total - free, 0), "free": free, "total": total}
    except Exception:
        return None


async def _get_vram_info() -> dict[str, int] | None:
    """Get GPU VRAM info ({used, free, total} MiB) or None when unmeasurable."""
    now = time.monotonic()
    if _state.vram and (now - _state.vram_check) < _VRAM_CACHE_TTL:
        return _state.vram

    vram = await asyncio.to_thread(_nvidia_smi_vram)
    if vram is None:
        vram = await _comfyui_vram()
    if vram:
        _state.vram = vram
        _state.vram_check = now
        return vram
    _state.vram = {}
    _state.vram_check = now
    return None


async def _has_vram_for_model(model: str) -> bool:
    """Check if there's enough VRAM for the given model.  Conservative estimates."""
    vram = await _get_vram_info()
    if not vram:
        return True  # can't check — allow it
    free_mb = vram.get("free", 0)
    # Rough VRAM estimates by model size
    if "smollm2" in model:
        return free_mb >= 512  # 360M model
    if "smollm3" in model:
        return free_mb >= 2048  # 3.1B model ~1.9GB
    if "embedding" in model:
        return free_mb >= 1024
    if "vl" in model or "vision" in model:
        return free_mb >= 4096  # vision models ~5GB
    if "8b" in model or "7b" in model:
        return free_mb >= 4096  # 7-8B Q4 ~5GB
    if "4b" in model:
        return free_mb >= 3072  # 4B Q4_K_M ~2.7GB incl. KV
    if "3.2" in model or "3b" in model:
        return free_mb >= 2048  # 3B Q4 ~2GB
    return free_mb >= 2048  # default conservative


async def _unload_idle_models() -> None:
    """Unload all running models to free VRAM (best effort).

    Tries the native /inference/unload endpoint first; on 404 (absent in the
    current docker/model-runner build) falls back to the Ollama-compatible
    API: list loaded models via /api/ps, then POST /api/chat with
    keep_alive=0 per model, which evicts immediately.
    """
    url = _dmr_base_url()
    try:
        client = await _get_client()
        resp = await client.post(f"{url}/inference/unload", json={"all": True}, timeout=5.0)
        if resp.status_code == 200:
            logger.info("DMR: unloaded idle models to free VRAM")
            return
    except Exception as exc:
        logger.debug("DMR unload failed (%s)", type(exc).__name__)
    # Fallback: Ollama-compatible API (verified live on
    # docker/model-runner:latest-vllm-cuda, 2026-09: /api/ps lists loaded
    # models; /api/chat with keep_alive=0 evicts right after serving).
    try:
        client = await _get_client()
        ps = await client.get(f"{url}/api/ps", timeout=5.0)
        ps.raise_for_status()
        loaded = [m.get("name") for m in ps.json().get("models", []) if m.get("name")]
        for name in loaded:
            try:
                await client.post(
                    f"{url}/api/chat",
                    json={
                        "model": name,
                        "messages": [{"role": "user", "content": "."}],
                        "options": {"num_predict": 1},
                        "keep_alive": 0,
                    },
                    timeout=30.0,
                )
            except Exception:
                logger.debug("DMR fallback unload failed for %s", name)
        if loaded:
            logger.info("DMR: unloaded %d model(s) via Ollama API fallback", len(loaded))
    except Exception as exc:
        logger.debug("DMR Ollama unload fallback failed (%s)", type(exc).__name__)


async def _free_gpu_memory() -> None:
    """Best-effort VRAM release before retrying a failed model load.

    Two sources of pressure on the shared 8GB card:
    - Other DMR runners still resident (DMR does not auto-unload;
      docker/model-runner#1014) — evicted via _unload_idle_models.
    - ComfyUI's cached checkpoints — released via POST /free, but only when
      its queue is idle so we don't kill an in-flight render.
    """
    await _unload_idle_models()
    base = getattr(settings, "COMFYUI_URL", "")
    if not base:
        return
    try:
        client = await _get_client()
        q = await client.get(f"{base.rstrip('/')}/queue", timeout=3.0)
        queue = q.json() if q.status_code == 200 else {}
        busy = len(queue.get("queue_running") or []) > 0
        if busy:
            logger.info("DMR OOM heal: ComfyUI busy — skipping /free")
            return
        await client.post(
            f"{base.rstrip('/')}/free",
            json={"unload_models": True, "free_memory": True},
            timeout=10.0,
        )
        logger.info("DMR OOM heal: freed ComfyUI model caches")
    except Exception as exc:
        logger.debug("DMR OOM heal: ComfyUI /free failed (%s)", type(exc).__name__)


# ── Per-request model routing (improvement #6) ────────────────────────────────

# Platform-aware routing tuned for the RTX 3070 8GB card.
#
# Two content tiers:
#   long-form  → DMR_TEXT_MODEL  (qwen3:8b, thinking model — best for professional
#                long copy, LinkedIn/Facebook posts, structured JSON)
#   short-form → DMR_MID_MODEL   (qwen3-4b-instruct-2507, non-thinking — fast,
#                punchy copy for Instagram/TikTok/X/Threads/YouTube; also the
#                chatbot model, kept warm via keep-alive 30m)
# Sub-200-char prompts on any platform still take the tiny model (smollm3),
# and schema requests always take the 8B regardless of platform.
LONG_FORM_PLATFORMS = frozenset({"linkedin", "facebook", "blog", "article"})
SHORT_FORM_PLATFORMS = frozenset({
    "instagram", "tiktok", "twitter", "x", "threads", "youtube", "pinterest",
})


def _select_model_by_complexity(
    prompt: str,
    schema: dict | None = None,
    model_override: str | None = None,
    platform: str | None = None,
) -> str:
    """Route to the appropriate model based on platform, task, and complexity.

    - Explicit model_override always wins.
    - Long-form platforms (linkedin, facebook) → DMR_TEXT_MODEL.
    - Short-form platforms (instagram, tiktok, x, threads, youtube) →
      DMR_MID_MODEL (non-thinking instruct — much faster than the 8B thinking
      model and already warm for chatbots). Schema requests on these platforms
      also route here: json_object mode constrains decode to valid JSON, so
      the 4B can't malform the flat caption/hashtag schemas they use.
    - JSON/schema with no platform hint → DMR_TEXT_MODEL (complex nested
      schemas like carousel outlines get the most capable local model).
    - Short prompts (<200 chars, no platform hint) → DMR_TINY_MODEL.
    - Everything else → DMR_TEXT_MODEL.
    """
    if model_override:
        return model_override

    p = platform.strip().lower() if platform else ""
    if p in SHORT_FORM_PLATFORMS:
        return settings.DMR_MID_MODEL
    if schema or p in LONG_FORM_PLATFORMS:
        return settings.DMR_TEXT_MODEL

    # Short prompts don't need a big model
    if len(prompt) < 200:
        return settings.DMR_TINY_MODEL

    return settings.DMR_TEXT_MODEL


# ── Benchmark caching (improvement #13) ───────────────────────────────────────

_benchmark_cache: dict[str, dict[str, float]] = {}  # model → {tps, timestamp}
_BENCH_CACHE_TTL = 3600.0  # 1 hour


async def get_model_benchmark(model: str, force: bool = False) -> dict[str, float] | None:
    """Get cached benchmark (TPS) for a model, or run one if stale."""
    now = time.time()
    cached = _benchmark_cache.get(model)
    if cached and not force and (now - cached.get("timestamp", 0)) < _BENCH_CACHE_TTL:
        return cached

    # Run a quick benchmark via CLI (non-blocking for the caller)
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "model", "bench", "--json", "--concurrency", "1", "--duration", "10s", model],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            data = json.loads(result.stdout)
            tps = 0.0
            if isinstance(data, list) and data:
                tps = data[0].get("tokens_per_second", 0)
            elif isinstance(data, dict):
                tps = data.get("tokens_per_second", 0)
            _benchmark_cache[model] = {"tps": tps, "timestamp": now}
            return _benchmark_cache[model]
    except Exception as exc:
        logger.debug("DMR benchmark failed (%s)", type(exc).__name__)
    return None


# ── Keep-alive configuration (improvement #10) ────────────────────────────────

_keep_alive_configured: set[str] = set()
_keep_alive_endpoint_warned: set[str] = set()


async def configure_keep_alive(model: str, keep_alive: str = "5m") -> None:
    """Set keep-alive for a model so it stays loaded between requests.

    Args:
        model: Model identifier (e.g. 'ai/qwen3:8b-q4_K_M')
        keep_alive: Duration string ('5m', '1h', '0' for immediate unload, '-1' for forever)
    """
    if model in _keep_alive_configured:
        return  # already configured

    url = _dmr_base_url()
    try:
        client = await _get_client()
        resp = await client.post(
            f"{url}/engines/_configure",
            json={"model": model, "keep_alive": keep_alive},
            timeout=5.0,
        )
        if resp.status_code in (200, 202):
            _keep_alive_configured.add(model)
            logger.info("DMR: keep_alive configured")
        elif resp.status_code == 404 and model not in _keep_alive_endpoint_warned:
            _keep_alive_endpoint_warned.add(model)
            logger.warning(
                "DMR: /engines/_configure returns 404 on this runner build — "
                "keep_alive for %s is owned by dmr-watchdog configs, not this call",
                sanitize_log_text(model, 120),
            )
    except Exception as exc:
        logger.debug("DMR keep_alive config failed (%s)", type(exc).__name__)


# ── Speculative decoding (improvement #12) ────────────────────────────────────

_speculative_configured: set[str] = set()


async def configure_speculative_decoding(
    model: str,
    draft_model: str = "hf.co/Qwen/Qwen3-0.6B-GGUF",
) -> None:
    """Configure speculative decoding: use a small draft model to speed up a larger one.

    This is a one-time per-model configuration.  The draft model proposes tokens
    that the target model verifies, giving 1.5-2x speedup on compatible hardware.

    NOTE: as of llama.cpp b9879/72874f559 the Qwen3-0.6B GGUF draft crashes the
    runner (``vector::_M_range_check`` during draft load), taking the target
    model offline until the draft config is removed.  Verify on the host before
    enabling in production.

    NOTE: ``docker model configure`` REPLACES the model's whole runtime config —
    re-apply context-size/keep-alive in the same call or they are lost.
    """
    key = f"{model}:{draft_model}"
    if key in _speculative_configured:
        return

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "model", "configure", "--speculative-draft-model", draft_model, model],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0:
            _speculative_configured.add(key)
            logger.info("DMR: speculative decoding configured")
        else:
            logger.debug("DMR speculative decoding failed (rc=%s)", result.returncode)
    except Exception as exc:
        logger.debug("DMR speculative decoding error (%s)", type(exc).__name__)


# ── Model warm-up (improvement #5) ───────────────────────────────────────────

_warmup_lock = asyncio.Lock()


def reset_warmup() -> None:
    """Allow warmup_models() to run again (used by the admin warmup endpoint)."""
    _state.warmup_done = False


def is_warmup_done() -> bool:
    """Whether the startup warmup pass has completed (or been marked done)."""
    return _state.warmup_done


async def warmup_models() -> None:
    """Pre-load frequently-used models into VRAM on startup.

    Sends a trivial prompt to each model so they're loaded and ready
    for the first real request.  Runs in background, non-blocking.

    VRAM-aware: on 8GB GPUs, only warm the text model (largest, most used).
    The tiny model (smollm3, ~2GB) loads quickly on first request.
    Vision model is only warmed if there's enough VRAM headroom.
    """
    if _state.warmup_done:
        return
    async with _warmup_lock:
        if _state.warmup_done:
            return
        _state.warmup_done = True

        if not await _check_dmr_health():
            logger.info("DMR warmup skipped — API offline")
            return

        # Apply best-practice runtime configurations before warming.
        # These match the `docker model configure` settings and ensure
        # the configs are applied even after a DMR restart.
        await apply_best_practice_configs()

        # Always warm the mid instruct (chatbots + short-form platform copy,
        # latency-critical, ~2.7GB resident). The 8B text model only joins
        # when the shared GPU genuinely has room — DMR doesn't auto-unload
        # (docker/model-runner#1014), so warming both while ComfyUI holds
        # VRAM guarantees the 8B load aborts and wastes the keep-alive slot.
        models_to_warm = [m for m in [getattr(settings, "DMR_MID_MODEL", "")] if m]

        # Rough resident-size estimates (MiB, weights + KV at configured ctx)
        _WARM_ESTIMATE = {"8b": 5500, "4b": 2700, "vl": 5000, "embedding": 1200, "smollm3": 2000, "3.2": 2200}

        def _est(model: str) -> int:
            low = model.lower()
            return next((v for k, v in _WARM_ESTIMATE.items() if k in low), 3000)

        vram = await _get_vram_info()
        free_mb = vram.get("free", 0) if vram else 1 << 20  # unknown → let heal path arbitrate
        budget_mb = free_mb - _est(getattr(settings, "DMR_MID_MODEL", "") or "")

        # 8B text model only when the shared GPU has room after the mid model —
        # DMR doesn't auto-unload (docker/model-runner#1014), so warming both
        # while ComfyUI holds VRAM just queues doomed loads.
        text_model = settings.DMR_TEXT_MODEL
        if text_model not in models_to_warm:
            if budget_mb >= _est(text_model) + 1000:  # +1GB headroom
                models_to_warm.append(text_model)
                budget_mb -= _est(text_model)
            else:
                logger.info(
                    "DMR warmup: skipping %s — only %dMB free after mid model",
                    text_model, budget_mb,
                )

        # Vision model is large — only warm on the leftover budget
        vision = settings.DMR_VISION_MODEL
        if budget_mb >= _est(vision) + 1000:
            models_to_warm.append(vision)

        for model in models_to_warm:
            try:
                # Keep-alive/context ownership belongs to dmr-watchdog's
                # apply_configs: the /inference/_configure endpoint is absent in
                # the current runner build (404), so setting it here would
                # silently no-op — and a hardcoded 5m would fight the 4B's
                # canonical 30m pin once the endpoint returns.
                # Send a trivial prompt to trigger model load
                await _call_dmr_chat_internal(
                    "Hi",
                    model_override=model,
                    max_tokens=5,
                    timeout=60.0,
                    _skip_health_check=True,
                )
                logger.info("DMR warmup: %s loaded", model)
            except Exception as exc:
                logger.debug("DMR warmup failed for %s (%s)", model, type(exc).__name__)


# ── Best-practice configuration (applied on startup) ────────────────────────────

# Best-practice runtime configs per model.
# These are applied via `docker model configure` CLI on startup to ensure
# optimal performance on the RTX 3070 8GB VRAM laptop.
#
# Key decisions (based on llama.cpp + Qwen3 best practices):
# - Context size 8192: enough for bot conversations with memory + brand RAG
# - n-gpu-layers 99: offload all layers to GPU (model fits in 8GB VRAM)
# - threads 8: match physical CPU cores
# - batch-size 1024: faster prompt processing
# - flash-attn on: reduces KV cache memory, speeds long contexts
# - keep-alive 5m: shorter than 10m to free VRAM faster on 8GB card
# - think mode for qwen3: enables reasoning mode (qwen3 is a thinking model)

_BEST_PRACTICE_CONFIGS: dict[str, dict[str, Any]] = {
    # 8B thinking model — long-form + schema. keep_alive 5m: the 8B serves
    # content bursts only (chatbots run on the mid model), and pinning it
    # would waste ~5 GB VRAM between bursts.
    # ctx 6144 leaves headroom for the pinned 4B + KV on the 8GB card.
    "ai/qwen3:8b-q4_K_M": {
        "context_size": 6144,
        "keep_alive": "5m",
        "think": True,
        "runtime_flags": ["--n-gpu-layers", "99", "--threads", "8", "--batch-size", "1024", "--flash-attn", "on"],
    },
    # 4B non-thinking instruct — short-form platform copy + ALL chatbots.
    # Pinned warm (30m): chatbot replies are latency-critical and arrive at
    # random intervals. ~2.7GB resident incl. KV at ctx 4096.
    "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M": {
        "context_size": 4096,
        "keep_alive": "30m",
        "runtime_flags": ["--n-gpu-layers", "99", "--threads", "8", "--batch-size", "1024", "--flash-attn", "on"],
    },
    "ai/llama3.2": {
        "context_size": 8192,
        "keep_alive": "5m",
        "runtime_flags": ["--n-gpu-layers", "99", "--threads", "8", "--batch-size", "1024", "--flash-attn", "on"],
    },
    "ai/qwen3-vl": {
        "context_size": 4096,
        "keep_alive": "5m",
        "runtime_flags": ["--n-gpu-layers", "99", "--threads", "8", "--batch-size", "512", "--flash-attn", "on"],
    },
    "ai/qwen3-embedding": {
        "keep_alive": "5m",
        "mode": "embedding",
        "runtime_flags": ["--n-gpu-layers", "99", "--threads", "8"],
    },
    "ai/smollm3": {
        "context_size": 4096,
        "keep_alive": "5m",
        "runtime_flags": ["--reasoning-budget", "0", "--n-gpu-layers", "99", "--threads", "4", "--batch-size", "512", "--flash-attn", "on"],
    },
}

# DMR model configs live in the runner's memory and are wiped when the model
# unloads (keep-alive expiry) or the runner restarts — so configs are pushed
# on a short TTL via _ensure_model_configured, not just once at startup.
_CONFIGURE_TTL = 60.0  # seconds


def _configure_payload(model: str) -> dict[str, Any] | None:
    """Build a POST /engines/_configure body for a model's canonical config.

    Schema mirrors scheduling.ConfigureRequest: hyphenated keys
    (context-size, runtime-flags), keep_alive as a Go duration string,
    thinking via llamacpp.reasoning-budget (-1 = unlimited, same as
    `docker model configure --think`).
    """
    cfg = _BEST_PRACTICE_CONFIGS.get(model)
    if cfg is None:  # tolerate short refs (e.g. "qwen3:8b-q4_K_M")
        norm = model.lower().removeprefix("docker.io/").removeprefix("huggingface.co/")
        cfg = next(
            (c for k, c in _BEST_PRACTICE_CONFIGS.items() if k.lower() in norm or norm in k.lower()),
            None,
        )
    if not cfg:
        return None
    body: dict[str, Any] = {"model": model}
    if "context_size" in cfg:
        body["context-size"] = cfg["context_size"]
    if "keep_alive" in cfg:
        body["keep_alive"] = cfg["keep_alive"]
    if cfg.get("mode"):
        body["mode"] = cfg["mode"]
    if cfg.get("think"):
        body["llamacpp"] = {"reasoning-budget": -1}
    if cfg.get("runtime_flags"):
        body["runtime-flags"] = cfg["runtime_flags"]
    return body


async def _ensure_model_configured(model: str, *, force: bool = False) -> None:
    """Push the canonical runtime config for a model via HTTP _configure.

    No-op for unlisted models and when re-applied within _CONFIGURE_TTL;
    pass force=True on the failure-recovery path since an unload or runner
    restart may have wiped the in-memory config.
    """
    body = _configure_payload(model)
    if body is None:
        return
    now = time.monotonic()
    if not force and (now - _state.configured.get(model, 0.0)) < _CONFIGURE_TTL:
        return
    try:
        client = await _get_client()
        resp = await client.post(
            f"{_dmr_base_url()}/engines/_configure", json=body, timeout=5.0
        )
        if resp.status_code in (200, 202):
            _state.configured[model] = now
        else:
            logger.debug("DMR _configure %s -> HTTP %s", model, resp.status_code)
    except Exception as exc:
        logger.debug("DMR _configure %s failed (%s)", model, type(exc).__name__)


async def apply_best_practice_configs() -> None:
    """Apply best-practice DMR configurations via the HTTP _configure endpoint.

    Uses POST /engines/_configure (not `docker model configure`) because the
    API container has no docker CLI — the old subprocess path silently
    no-op'd here and configs only ever landed via dmr-watchdog. Idempotent
    per-process; per-request _ensure_model_configured keeps them alive
    across model unloads and runner restarts.
    """
    if getattr(apply_best_practice_configs, "_done", False):
        return
    apply_best_practice_configs._done = True  # type: ignore[attr-defined]

    for model in _BEST_PRACTICE_CONFIGS:
        await _ensure_model_configured(model, force=True)


# ── Core chat with retry + CLI fallback (improvements #1, #4, #7, #9) ──────────


async def _call_dmr_chat_internal(
    prompt: str,
    *,
    model_override: str | None = None,
    system: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.7,
    top_p: float = 1.0,
    schema: dict | None = None,
    tools: list[dict] | None = None,
    stream: bool = False,
    timeout: float = 180.0,
    platform: str | None = None,
    _skip_health_check: bool = False,
) -> dict[str, Any]:
    """Internal DMR chat call with retry, CLI fallback, streaming, and tool calling.

    Returns:
        {"text": str} for plain text responses
        {"json": dict} for schema/JSON responses
        {"tool_calls": list} for tool-calling responses
        {"stream": async_generator} for streaming responses
    """
    # Improvement #6: per-request model routing (platform-aware)
    model = _select_model_by_complexity(prompt, schema, model_override, platform)

    # Improvement #2: pre-flight health check
    if not _skip_health_check and not await _check_dmr_health():
        # Improvement #1: CLI fallback
        logger.info("DMR API offline — falling back to CLI")
        cli_result = _dmr_cli_run(model, prompt, timeout=int(timeout))
        if cli_result is not None:
            if schema:
                return _parse_json_response(cli_result)
            return {"text": cli_result}
        raise ConnectionError("DMR is offline (both API and CLI fallback failed)")

    # Build the system prompt
    sys_prompt = system or (
        "You are a helpful assistant. When asked to return JSON, "
        "output only valid JSON — no markdown, no explanation."
    )
    user_msg = prompt
    if schema:
        user_msg += "\n\nIMPORTANT: Return ONLY valid JSON matching the requested structure. No markdown code blocks."
        user_msg += " /no_think"
    elif "qwen3" in model.lower():
        # Qwen3 thinks by default even for chat — suppress so replies don't
        # contain reasoning blocks (think tags are also stripped below).
        user_msg += " /no_think"

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": user_msg},
    ]
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    else:
        payload["max_tokens"] = 4096
    if schema:
        payload["response_format"] = {"type": "json_object"}
    # Improvement #9: tool calling support
    if tools:
        payload["tools"] = tools

    # Improvement #11: VRAM-aware routing
    if not await _has_vram_for_model(model):
        logger.warning("DMR: insufficient VRAM — unloading idle models")
        await _unload_idle_models()
        if not await _has_vram_for_model(model):
            # Fall back to tiny model
            tiny = settings.DMR_TINY_MODEL
            if await _has_vram_for_model(tiny):
                logger.warning("DMR: falling back to tiny model")
                payload["model"] = tiny
                model = tiny

    # Re-push the canonical runtime config — DMR drops it on model unload
    # (keep-alive expiry) and runner restart (docker/model-runner#726).
    await _ensure_model_configured(model)

    # Improvement #7: streaming support
    if stream:
        return {"stream": _stream_dmr_chat(payload, timeout)}

    url = f"{settings.DMR_URL}/chat/completions"

    # Improvement #4: retry on cold-start timeout
    last_exc: Exception | None = None
    sem = _get_semaphore()
    for attempt in range(2):  # 1 retry
        try:
            client = await _get_client()
            async with sem:
                resp = await client.post(url, json=payload, timeout=timeout)
            if resp.status_code != 200:
                body = resp.text[:400]
                _invalidate_health_cache()
                if attempt == 0 and _RUNNER_LOAD_RE.search(body):
                    # Runner failed to start (typically CUDA OOM while DMR
                    # held another model resident or the in-memory config
                    # was wiped) — free VRAM, re-push config, then retry.
                    logger.warning("DMR runner load failed for %s — healing GPU state", model)
                    await _free_gpu_memory()
                    await _ensure_model_configured(model, force=True)
                raise ConnectionError(f"DMR error {resp.status_code}: {body}")

            data = resp.json()
            msg = data["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""

            # Tool calling response
            if msg.get("tool_calls"):
                return {"tool_calls": msg["tool_calls"], "text": _strip_think_tags(content)}

            if schema:
                return _parse_json_response(content)
            return {"text": _strip_think_tags(content)}

        except (httpx.TimeoutException, httpx.ConnectError, ConnectionError) as exc:
            last_exc = exc
            if attempt == 0:
                # Cold-start retry: model is now loading, wait and retry
                logger.info("DMR cold-start retry (attempt %s)", attempt + 1)
                await asyncio.sleep(2.0)
                continue
            _invalidate_health_cache()
            # Improvement #1: CLI fallback on connection failure
            if isinstance(exc, httpx.ConnectError | ConnectionError):
                logger.info("DMR API failed — falling back to CLI")
                cli_result = _dmr_cli_run(model, prompt, timeout=int(timeout))
                if cli_result is not None:
                    if schema:
                        return _parse_json_response(cli_result)
                    return {"text": cli_result}
            raise ConnectionError(f"DMR failed after retry: {exc}") from exc
        except Exception as exc:
            last_exc = exc
            raise

    raise ConnectionError(f"DMR failed after retries: {last_exc}")


async def _stream_dmr_chat(payload: dict, timeout: float):
    """Stream chat completion tokens from DMR (improvement #7).

    Yields content chunks as they arrive (SSE format).
    """
    url = f"{settings.DMR_URL}/chat/completions"
    payload = {**payload, "stream": True}
    client = await _get_client()
    async with client.stream("POST", url, json=payload, timeout=timeout) as resp:
        if resp.status_code != 200:
            _invalidate_health_cache()
            raise ConnectionError(f"DMR stream error {resp.status_code}")
        async for line in resp.aiter_lines():
            if line.startswith("data: "):
                data_str = line[6:]
                if data_str == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue


# ── Public chat API ──────────────────────────────────────────────────────────


async def call_dmr_chat(
    prompt: str,
    *,
    schema: dict | None = None,
    model_override: str | None = None,
    max_tokens: int | None = None,
    system: str | None = None,
    tools: list[dict] | None = None,
    stream: bool = False,
    temperature: float = 0.7,
    platform: str | None = None,
) -> dict[str, Any]:
    """Call DMR for chat completion with all improvements active.

    This is the main entry point for text inference via DMR.
    ``platform`` (e.g. "linkedin", "instagram") selects the content tier:
    long-form platforms → the 8B model, short-form → the mid instruct model.

    Returns:
        {"text": str} for plain text
        {"json": dict} for schema/JSON responses
        {"tool_calls": list, "text": str} for tool-calling responses
        {"stream": async_generator} for streaming responses
    """
    dmr_timeout = 180.0 if schema else 30.0
    return await _call_dmr_chat_internal(
        prompt,
        model_override=model_override,
        system=system,
        max_tokens=max_tokens,
        temperature=temperature,
        schema=schema,
        tools=tools,
        stream=stream,
        timeout=dmr_timeout,
        platform=platform,
    )


async def call_dmr_vllm_chat(
    prompt: str,
    *,
    system: str | None = None,
    model_override: str | None = None,
    max_tokens: int | None = None,
    temperature: float = 0.7,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """EXPERIMENTAL: chat via the vLLM backend on the GPU runner.

    vLLM serves safetensors models only (GGUF models belong to llama.cpp via
    call_dmr_chat). This path is manual-selection only — it is NOT in the
    automatic fallback chain. On an 8GB card vLLM runs at
    gpu-memory-utilization 0.25 (configured via the full model ref
    `docker.io/ai/smollm2-vllm:latest` — once a model has a runtime config,
    DMR resolves ONLY the full ref; short names 404). It still competes
    with llama.cpp-loaded GGUF models; keep one loaded at a time.
    """
    url = getattr(settings, "DMR_VLLM_URL", "") or ""
    if not url:
        raise ConnectionError("DMR vLLM backend is not configured (DMR_VLLM_URL)")
    model = model_override or "docker.io/ai/smollm2-vllm:latest"
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens or 4096,
    }
    client = await _get_client()
    async with _get_semaphore():
        resp = await client.post(f"{url}/chat/completions", json=payload, timeout=timeout)
    if resp.status_code != 200:
        raise ConnectionError(f"DMR vLLM error {resp.status_code}: {resp.text[:400]}")
    content = resp.json()["choices"][0]["message"].get("content") or ""
    return {"text": _strip_think_tags(content), "model": model, "backend": "vllm"}


# ── Embeddings ───────────────────────────────────────────────────────────────


async def call_dmr_embedding(
    text: str,
    model_override: str | None = None,
) -> list[float]:
    """Generate embeddings via DMR with CLI fallback and connection pooling."""
    model = model_override or settings.DMR_EMBEDDING_MODEL

    if not await _check_dmr_health():
        raise ConnectionError("DMR is offline — embeddings unavailable via CLI fallback")

    await _ensure_model_configured(model)
    url = f"{settings.DMR_URL}/embeddings"
    try:
        client = await _get_client()
        async with _get_semaphore():
            resp = await client.post(
                url,
                json={"model": model, "input": text},
                timeout=120.0,
            )
        if resp.status_code == 200:
            return resp.json()["data"][0]["embedding"]
        _invalidate_health_cache()
        raise ConnectionError(f"DMR embedding error {resp.status_code}: {resp.text[:400]}")
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        _invalidate_health_cache()
        raise ConnectionError(f"DMR embedding connection error: {exc}") from exc


# ── Vision (improvement #8: consolidated) ────────────────────────────────────


async def call_dmr_vision(
    image_data_uri: str,
    prompt: str,
    *,
    max_tokens: int = 60,
    temperature: float = 0.3,
    model_override: str | None = None,
) -> str | None:
    """Call DMR vision model (qwen3-vl) with an image and text prompt.

    Consolidates the duplicate _dmr_vision_query functions from
    image_enhance.py and media_ai.py into a single shared helper.

    Args:
        image_data_uri: Data URI (data:image/jpeg;base64,...) or raw base64 string
        prompt: Question or instruction about the image
        max_tokens: Max tokens to generate
        temperature: Sampling temperature
        model_override: Override the default vision model

    Returns:
        Text response, or None on failure (non-raising for graceful fallback)
    """
    model = model_override or settings.DMR_VISION_MODEL

    # Ensure it's a data URI
    if not image_data_uri.startswith("data:"):
        image_data_uri = f"data:image/jpeg;base64,{image_data_uri}"

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_uri}},
                ],
            },
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    # VRAM check — vision models are large
    if not await _has_vram_for_model(model):
        logger.warning("DMR vision: insufficient VRAM")
        await _unload_idle_models()

    await _ensure_model_configured(model)
    url = f"{settings.DMR_URL}/chat/completions"
    try:
        client = await _get_client()
        async with _get_semaphore():
            resp = await client.post(url, json=payload, timeout=300.0)
        if resp.status_code == 200:
            msg = resp.json()["choices"][0]["message"]
            return _strip_think_tags(msg.get("content") or msg.get("reasoning_content") or "")
        logger.warning("DMR vision returned %s", resp.status_code)
    except Exception as exc:
        logger.warning("DMR vision failed (%s)", type(exc).__name__)
    return None


# ── Request logging (improvement #14) ────────────────────────────────────────


async def get_dmr_requests(
    model: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch recent DMR request/response pairs for debugging.

    Uses `docker model requests` CLI since there's no HTTP endpoint for this.
    """
    cmd = ["docker", "model", "requests"]
    if model:
        cmd.extend(["--model", model])

    try:
        result = await asyncio.to_thread(
            subprocess.run,
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return []

        # Parse the CLI output (line-delimited JSON or plain text)
        lines = result.stdout.strip().split("\n")[:limit]
        requests = []
        for line in lines:
            try:
                requests.append(json.loads(line))
            except json.JSONDecodeError:
                requests.append({"raw": line})
        return requests
    except Exception as exc:
        logger.debug("DMR requests fetch failed (%s)", type(exc).__name__)
        return []


# ── JSON parsing helper ───────────────────────────────────────────────────────


def _parse_json_response(content: str) -> dict[str, Any]:
    """Parse JSON from a model response, handling markdown code blocks."""
    # Strip markdown code fences if present
    cleaned = content.strip()
    if cleaned.startswith("```"):
        # Remove first line (```json or ```) and last line (```)
        lines = cleaned.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Try to find JSON object in the text
        match = re.search(r"\{[\s\S]*\}", cleaned)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass
        # Return raw content as text if we can't parse JSON
        return {"text": content, "_parse_error": True}


# ── Startup/shutdown hooks ───────────────────────────────────────────────────


async def on_startup() -> None:
    """Call on FastAPI startup to warm up DMR models."""
    # Run warmup in background so it doesn't block startup
    asyncio.create_task(warmup_models())


async def on_shutdown() -> None:
    """Call on FastAPI shutdown to clean up resources."""
    await close_client()

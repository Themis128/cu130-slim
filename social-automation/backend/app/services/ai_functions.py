"""Databricks-pattern AI functions on the local DMR — SQL-callable-style
functions the stack uses as the AI-Functions layer (adopts ops).

    fix_grammar / ai_classify / ai_extract / ai_mask / ai_similarity
    media_gate — the composed publish gate (grammar → nlp → similarity)

Transport: Docker Model Runner OpenAI-compatible endpoints.
    DMR_BASE_URL   e.g. http://localhost:12435/engines/v1/chat/completions
    DMR_EMBED_URL  e.g. http://localhost:12435/engines/v1/embeddings
    DMR_MODEL      chat model (default ai/llama3.2)
import re as _re
    DMR_EMBED_MODEL embedding model (default docker.io/ai/qwen3-embedding:latest)

Every call is a guarded async op — failures return a safe default (never
block a publish path on a model hiccup) and log a warning.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)

_TIMEOUT = 90.0


def _audit(fn: str, outcome: str, latency_ms: float, input_text: str = "") -> None:
    """Fire-and-forget row into ai_function_calls — never blocks the caller.
    input is stored as a sha (never raw — same PII boundary as the lake)."""
    import asyncio
    import hashlib

    async def _write() -> None:
        try:
            from app.models.ai_function_call import AIFunctionCall

            from app.db.session import async_session_maker

            _, _, model, _ = _cfg()
            async with async_session_maker() as s:
                s.add(
                    AIFunctionCall(fn=fn, model=model, outcome=outcome, latency_ms=latency_ms, input_sha=hashlib.sha256(input_text.encode()).hexdigest()[:32])
                )
                await s.commit()
        except Exception as e:
            logger.debug("ai_function audit write failed: %s", e)

    try:
        asyncio.get_running_loop().create_task(_write())
    except RuntimeError:
        pass  # no loop — audit skipped, call itself succeeded


class _timed:
    """async context manager: wraps each public fn, audits outcome+latency."""

    def __init__(self, fn: str, text: str):
        self.fn, self.text = fn, text

    async def __aenter__(self):
        import time

        self._t0 = time.monotonic()
        return self

    async def __aexit__(self, exc, *_):
        import time

        _audit(self.fn, "error" if exc else "ok", (time.monotonic() - self._t0) * 1000, self.text)
        return False  # never swallow — the function's own guards still apply


def _cfg() -> tuple[str, str, str, str]:
    s = get_settings()
    base = (getattr(s, "DMR_BASE_URL", "") or "http://localhost:12435/engines/v1/chat/completions").strip()
    embed = (getattr(s, "DMR_EMBED_URL", "") or "http://localhost:12435/engines/v1/embeddings").strip()
    model = (getattr(s, "DMR_MODEL", "") or "ai/llama3.2").strip()
    embed_model = (getattr(s, "DMR_EMBED_MODEL", "") or "docker.io/ai/qwen3-embedding:latest").strip()
    return base, embed, model, embed_model


async def _chat(prompt: str, max_tokens: int = 200, quality: bool = False) -> str:
    base, _, model, _ = _cfg()
    if quality:
        qm = (getattr(get_settings(), "DMR_QUALITY_MODEL", "") or "docker.io/ai/qwen3:8b-q4_K_M").strip()
        if qm:
            model = qm
    # DMR is flaky under load — an empty body or transient failure is common
    # while the engine hot-swaps models. Retry once so agents see the serving
    # model, not the swap window.
    import asyncio as _aio
    timeout = _TIMEOUT * 3 if quality else _TIMEOUT
    last_err: Exception | None = None
    for attempt in range(2):
        try:
            async with httpx.AsyncClient(timeout=timeout) as c:
                r = await c.post(
                    base,
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": 0.0,
                        "max_tokens": max_tokens,
                    },
                )
            return r.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            last_err = e
            if attempt == 0:
                await _aio.sleep(2)
    raise last_err  # type: ignore[misc]


async def _embed(text: str) -> list[float]:
    _, embed, _, embed_model = _cfg()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(embed, json={"model": embed_model, "input": text[:1500]})
    return r.json()["data"][0]["embedding"]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1)


async def fix_grammar(text: str) -> str:
    """Correct spelling/grammar only — preserves meaning, proper nouns,
    hashtags, URLs, numbers exactly."""
    import time as _t

    t0 = _t.monotonic()
    try:
        out = await _chat(
            "Fix ONLY spelling and grammar. Preserve meaning, proper nouns, hashtags, URLs, numbers EXACTLY. Return ONLY the corrected text.\n\n" + text
        )
        _audit("fix_grammar", "ok", (_t.monotonic() - t0) * 1000, text)
        return out
    except Exception as e:
        logger.warning("fix_grammar failed: %s", e)
        _audit("fix_grammar", "error", (_t.monotonic() - t0) * 1000, text)
        return text


async def ai_classify(labels: list[str] | str, text: str) -> str:
    """Databricks-style classify — pick ONE of the given labels by intent."""
    if isinstance(labels, str):
        labels = [x.strip() for x in labels.split(",")]
    opts = " / ".join(labels)
    import time as _t

    t0 = _t.monotonic()
    try:
        out = await _chat(
            "Classify the text's INTENT. Pick EXACTLY ONE label from "
            f"[{opts}]. Judge by what the sender wants — asking for help, "
            "pricing, or a service = buying interest, not a casual note. "
            f"Output ONLY the label.\n\nText: {text[:1200]}",
            12,
        )
        _audit("ai_classify", "ok", (_t.monotonic() - t0) * 1000, text)
        return out if out in labels else labels[0]
    except Exception as e:
        logger.warning("ai_classify failed: %s", e)
        _audit("ai_classify", "error", (_t.monotonic() - t0) * 1000, text)
        return labels[0] if labels else ""


async def ai_extract(field: str, text: str, quality: bool = False) -> str:
    """Extract a single field from text — NONE if absent.
    quality=True routes to the bigger DMR model (Q&A, scoring)."""
    import time as _t

    t0 = _t.monotonic()
    try:
        out = await _chat(
            "You are a precise extractor. From the text, extract only the "
            "requested field. If absent output NONE. Output ONLY the value, "
            f"no prose.\nField: {field}\n\nText: {text[:1200]}",
            30,
            quality=quality,
        )
        _audit("ai_extract", "ok", (_t.monotonic() - t0) * 1000, text)
        return out
    except Exception as e:
        logger.warning("ai_extract failed: %s", e)
        _audit("ai_extract", "error", (_t.monotonic() - t0) * 1000, text)
        return "NONE"

import re as _re

_RE_PII = _re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
    r"|\+?\d[\d\s().-]{7,}\d")


def _pii_floor(text: str) -> str:
    """Model-independent PII floor — emails/phones redacted regardless of
    DMR health. Applied on top of whatever the model returns (and alone
    when it fails) so no caller ever sees unmasked PII."""
    return _RE_PII.sub("[REDACTED]", text)


async def ai_mask(text: str, labels: str = "person,email,phone") -> str:
    """Mask PII spans — model pass + regex floor, always returns masked."""
    import time as _t

    t0 = _t.monotonic()
    try:
        out = await _chat(
            f"Redact every {labels} in the text to [REDACTED-<type>]. Keep all other content identical. Output ONLY the redacted text.\n\n" + text
        )
        _audit("ai_mask", "ok", (_t.monotonic() - t0) * 1000, text)
        return _pii_floor(out)
    except Exception as e:
        logger.warning("ai_mask failed: %s", e)
        _audit("ai_mask", "error", (_t.monotonic() - t0) * 1000, text)
        return _pii_floor(text)


async def ai_similarity(a: str, b: str) -> float:
    """Embedding-cosine similarity — the cross-format dedup signal."""
    import time as _t

    t0 = _t.monotonic()
    try:
        ea, eb = await _embed(a), await _embed(b)
        _audit("ai_similarity", "ok", (_t.monotonic() - t0) * 1000, a)
        return round(_cos(ea, eb), 4)
    except Exception as e:
        logger.warning("ai_similarity failed: %s", e)
        _audit("ai_similarity", "error", (_t.monotonic() - t0) * 1000, a)
        return 0.0


async def media_gate(text: str, recent_texts: list[str] | None = None, nlp_min: int = 80, sim_max: float = 0.85) -> dict[str, Any]:
    """The composed publish gate: grammar-fix → nlp-score → similarity.

    Returns {"ok": bool, "text": cleaned, "score": nlp, "similarity": max_sim,
             "reason": str|None} — ok=False means the piece must NOT publish.
    """
    clean = await fix_grammar(text)
    score_text = await ai_extract("a plain-English quality score 0-100 for a social post", clean, quality=True)
    try:
        score = int("".join(ch for ch in score_text if ch.isdigit()) or "0")
    except ValueError:
        score = 0
    sim = 0.0
    for prev in (recent_texts or [])[:10]:
        sim = max(sim, await ai_similarity(clean, prev))
        if sim >= sim_max:
            break
    ok = score >= nlp_min and sim < sim_max
    reason = None if ok else (f"nlp {score}<{nlp_min}" if score < nlp_min else f"sim {sim}>={sim_max}")
    return {"ok": ok, "text": clean, "score": score, "similarity": sim, "reason": reason}

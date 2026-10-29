"""Loop-engineering harness for LLM calls (Shift.Forward adoption #3).

Wraps dmr.chat in a structured control loop:
- single-responsibility prompts (one job per call)
- bounded retry with context trim on repeat
- deterministic post-checks before returning

    result = await llm_call(
        prompt, system=..., model=...,
        expect=lambda t: len(t) > 10,      # semantic check
        max_retries=3, trim_chars=4000,     # context trim on retry
        tag="survey-subject")
"""

from __future__ import annotations

import logging
from collections.abc import Callable

logger = logging.getLogger(__name__)

DEFAULT_TRIM = 4000
DEFAULT_RETRIES = 3


async def llm_call(
    prompt: str,
    *,
    system: str = "",
    model: str | None = None,
    expect: Callable[[str], bool] | None = None,
    max_retries: int = DEFAULT_RETRIES,
    trim_chars: int = DEFAULT_TRIM,
    tag: str = "llm",
) -> str:
    """One LLM call wrapped in a control loop.

    Retries on: empty/nonconforming output (expect() false), transport error.
    Each retry trims the prompt to `trim_chars` (context-management on
    repeat — loop engineering's context-degradation guard).
    """
    from app.services import dmr

    if len(prompt) > trim_chars:
        prompt = prompt[:trim_chars]
        logger.debug("%s: prompt trimmed to %d", tag, trim_chars)

    last_err: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            try:
                resp = await dmr.call_dmr_chat(prompt, system=system or None, model_override=model)
                text = resp.get("text") if isinstance(resp, dict) else str(resp)
            except Exception:
                # service payload shape (tools/schema params) can hang on
                # small models — fall back to a bare chat call
                import os

                import httpx

                url = os.environ.get("DMR_URL", "http://host.docker.internal:12435/engines/llama.cpp/v1")
                m = model or os.environ.get("DMR_TEXT_MODEL", "docker.io/ai/smollm2:latest")
                msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}]
                async with httpx.AsyncClient(timeout=60) as c:
                    r = await c.post(f"{url}/chat/completions", json={"model": m, "messages": msgs, "max_tokens": 500})
                d = r.json()
                text = (d.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as e:
            last_err = e
            logger.warning("%s: attempt %d transport error: %s", tag, attempt, e)
            continue
        text = (text or "").strip()
        if not text:
            logger.warning("%s: attempt %d empty output", tag, attempt)
            continue
        if expect and not expect(text):
            logger.warning("%s: attempt %d failed expect()", tag, attempt)
            continue
        return text

    if last_err:
        raise last_err
    return ""


def single_job_prompt(job: str, context: str = "", constraint: str = "") -> str:
    """Build a single-responsibility prompt (loop engineering's SRP rule)."""
    parts = [f"Your only job: {job}."]
    if constraint:
        parts.append(f"Constraint: {constraint}.")
    if context:
        parts.append(context)
    return "\n".join(parts)

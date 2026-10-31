"""content_eval.py — LLM-judge eval on generated posts (the MLflow-eval adopt).

Scores a draft on voice/dup/clarity/CTA 0-10 via the shared `call_inference`
path; brand_agent rejects below threshold — a quantitative quality gate vs the
heuristic-only slop-scan.
"""
from __future__ import annotations

import json

from sqlalchemy.ext.asyncio import AsyncSession

RUBRIC = (
    "Score this social post 0-10 on: 1) brand-voice fit (plain, direct, no "
    "AI-cliches), 2) specificity (one concrete detail vs generic claims), "
    "3) readability (sentence length, structure), 4) CTA clarity. "
    "Reply with ONLY a JSON: {\"total\":N,\"voice\":N,\"specificity\":N,"
    "\"readability\":N,\"cta\":N,\"why\":\"one phrase\"}."
)


async def eval_post_async(content: str, db: AsyncSession) -> int:
    """Return the LLM-judge total score 0-10 for a post draft; -1 on failure."""
    from app.services.inference import call_inference

    result = await call_inference(
        prompt=RUBRIC + "\n\nPost:\n" + content[:2000],
        provider_name="dmr",
        db=db,
        brand_context="",
        max_tokens=140,
    )
    raw = str(result.get("response", ""))
    i, j = raw.find("{"), raw.rfind("}") + 1
    if i < 0 or j <= i:
        return -1
    return int(json.loads(raw[i:j]).get("total", 0))

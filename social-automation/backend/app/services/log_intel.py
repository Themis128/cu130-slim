"""syslog→LLM intelligence — Shift.Forward adoption #4.

Pulls recent error/warning logs from Loki, runs them through the
loop-engineered LLM triage, and posts a digest to Slack. The vectorize
step is deferred — recent-window query works without embeddings.

    summary = await triage_logs(minutes=15)
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from app.services.loop_harness import llm_call, single_job_prompt

logger = logging.getLogger(__name__)

LOKI = os.environ.get("LOKI_URL", "http://192.168.1.130:3100")
# broad selector — every service's stdout/stderr lands here
QUERY = '{service_name=~".+"} |~ "error|Error|ERROR|failed|exception|warning|WARNING"'
MAX_LINES = 40


async def _query_loki(minutes: int) -> list[str]:
    """Pull the last N minutes of error/warning lines from Loki."""
    end = int(__import__("time").time() * 1e9)
    start = end - int(minutes * 60 * 1e9)
    params = {
        "query": QUERY,
        "start": start,
        "end": end,
        "limit": MAX_LINES,
        "direction": "backward",
    }
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.get(f"{LOKI}/loki/api/v1/query_range", params=params)
        if r.status_code != 200:
            logger.warning("loki %s: %s", r.status_code, r.text[:120])
            return []
    lines = []
    for stream in r.json().get("data", {}).get("result") or []:
        labels = stream.get("stream", {})
        svc = labels.get("container_name") or labels.get("service") or labels.get("job", "?")
        for _, line in stream.get("values", []):
            lines.append(f"[{svc}] {line[:200]}")
    return lines[:MAX_LINES]


async def triage_logs(minutes: int = 15) -> dict[str, Any]:
    """Loki → LLM triage digest. Returns {'lines': n, 'summary': text}."""
    lines = await _query_loki(minutes)
    if not lines:
        return {"lines": 0, "summary": "no errors/warnings in window"}

    context = "\n".join(lines)
    prompt = single_job_prompt(
        "triage these log lines — group by service, flag the top 3 issues with severity (P1/P2/P3) and a one-line cause guess. Max 150 words.",
        context=context,
        constraint="be terse; no markdown tables; emoji per severity",
    )
    try:
        summary = await llm_call(prompt, expect=lambda t: len(t) > 20, tag="log-triage", model="docker.io/ai/smollm2:latest")
    except Exception as e:
        summary = f"triage failed: {e}"
    return {"lines": len(lines), "summary": summary}

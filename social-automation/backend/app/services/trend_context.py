"""Live trend-signal context for content generation.

The trend-driven-media rule requires every post to cite a live signal —
but raw trending feeds are mostly niche-irrelevant noise (Greek politics,
sports). This helper runs the trendwatch fetch + niche-matrix filter and
returns the top QUALIFIED signal (or None — better no citation than a
forced one). Results are cached for 6h so per-action generation doesn't
refetch Google Trends every call.
"""

from __future__ import annotations

import time
from typing import Any

_TTL = 6 * 3600
_cache: tuple[float, str | None] = (0.0, None)


def top_relevant_signal(geos: tuple[str, ...] = ("GR", "US")) -> str | None:
    """Return the highest-traffic niche-relevant trend query, or None."""
    now = time.monotonic()
    cached_at, cached = _cache
    if cached is not None and now - cached_at < _TTL:
        return cached
    try:
        import sys

        if "/app" not in sys.path:
            sys.path.insert(0, "/app")
        from scripts.trendwatch import build_matrix, fetch_trending

        signals: list[dict[str, Any]] = []
        for geo in geos:
            try:
                signals.extend(fetch_trending(geo))
            except Exception:  # noqa: BLE001 — one geo down ≠ no signal
                continue
        relevant = [r for r in build_matrix(signals) if r.get("relevant")]
        top = max(relevant, key=lambda r: r.get("traffic") or 0, default=None)
        signal = (top or {}).get("query")
    except Exception:  # noqa: BLE001 — infra down → no citation, don't fabricate
        signal = None
    globals()["_cache"] = (now, signal or "")
    return signal or None


def trend_line() -> str:
    """Prompt fragment naming the live signal, or empty string."""
    s = top_relevant_signal()
    return (
        f"\n\nA live trend signal readers are searching now: \"{s}\". "
        "Reference it only if it fits the instruction naturally — never force it."
        if s
        else ""
    )

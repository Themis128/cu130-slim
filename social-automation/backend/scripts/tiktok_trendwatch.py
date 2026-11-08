"""TikTok trendwatch — weekly trending-video signals for content strategy.

Same three-tier model as scripts/trendwatch.py (Chernenko & Babenko
trendwatching), but Stage 1 pulls from the TikTok Creative Center Trends
feed instead of Google Trends RSS:

  Tier 1  Signal Identification — unsigned CreativeOne report endpoints,
          US weekly top videos across metric/label sweeps.
  Tier 2  Analytical Core — same niche matrix (build_matrix) — a video
          only qualifies when its hook text touches the Cloudless niche.
  Tier 3  Scenario Modelling — TikTok-channel actions: the hook/format is
          the reusable asset, not the topic. Scenarios carry format
          guidance (3s hook, vertical, spoken keywords for TikTok SEO,
          sound-on, is_aigc flag for generated clips).

Honest data note (verified 2026-11-08): logged-out tier is US-only, 4
videos per call — breadth comes from the param sweep, ~30-80 distinct
videos/week. The feed favors promoted content; engagement-rate ranking
surfaces small organic-format winners — keep both orderings.

Run inside the social-api container:
    docker exec social-api python scripts/tiktok_trendwatch.py
    docker exec social-api python scripts/tiktok_trendwatch.py --slack
    docker exec social-api python scripts/tiktok_trendwatch.py --full   # +organicOnly sweep
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.tiktok_trends import (  # noqa: E402
    DEFAULT_SWEEP,
    FULL_SWEEP,
    fetch_trending_videos,
)
from scripts.trendwatch import build_matrix  # noqa: E402

FORMAT_GUIDE = (
    "reusable format: vertical 9:16 clip — hook in first 1-3s, on-screen "
    "text with searchable keywords (TikTok SEO), sound ON, 3-5 niche "
    "hashtags; set is_aigc=true when the clip is generated"
)


def model_scenarios(rows: list[dict]) -> list[dict]:
    """Tier 3 — TikTok-channel actions for qualified signals.

    early_window -> benchmark-market hook: pre-draft the format; US trend
                    precedes GR/EU availability per the diffusion finding.
    opportunity   -> produce within the weekly trend window. The asset to
                    copy is the FORMAT (hook line + structure), never the
                    creator's topic wholesale.
    """
    scenarios = []
    for r in rows:
        if r["classification"] == "noise":
            continue
        tt = r.get("tiktok") or {}
        strategy = "leader" if r["classification"] == "opportunity" else "fast_follower"
        scenarios.append(
            {
                "query": r["query"],
                "geo": r["geo"],
                "traffic": r["traffic"],
                "classification": r["classification"],
                "swot": r["swot"],
                "strategy": strategy,
                "innovative_value": f"hook: {r['query'][:90]}",
                "target_segment": "TikTok organic discovery (FYP) → funnel",
                "action": ("produce vertical clip inside the weekly window" if strategy == "leader" else "pre-draft format for the GR/EU lag wave"),
                "channel": "tiktok",
                "format_guide": FORMAT_GUIDE,
                "reference": tt.get("url") or "",
                "creator": tt.get("author") or "",
                "engagement_rate": tt.get("engagement_rate"),
                "cta": "utm_source=tiktok&utm_medium=social&utm_campaign=trend-" + re.sub(r"[^a-z0-9]+", "-", r["query"].lower())[:40],
            }
        )
    scenarios.sort(key=lambda s: (-(s["classification"] == "opportunity"), -s["traffic"]))
    return scenarios


def render(scenarios: list[dict], rows: list[dict]) -> str:
    total = len(rows)
    relevant = sum(1 for r in rows if r["relevant"])
    lines = [
        f"TikTok Trendwatch — {datetime.datetime.now(datetime.UTC):%Y-%m-%dT%H:%MZ}",
        f"videos {total} | niche-relevant {relevant} | actionable {len(scenarios)}",
        "",
    ]
    if not scenarios:
        lines.append("no actionable TikTok signals this sweep — monitor only")
        return "\n".join(lines)
    for s in scenarios:
        marker = "LEADER" if s["strategy"] == "leader" else "FAST-FOLLOW"
        er = f" ER {s['engagement_rate']:.2%}" if s.get("engagement_rate") else ""
        lines.append(f"[{marker}] {s['query'][:80]} ({s['traffic']:,} wk views{er})")
        if s.get("creator"):
            lines.append(f"  ref: @{s['creator']} — {s['reference']}")
        lines.append(f"  action: {s['action']} | CTA {s['cta']}")
    return "\n".join(lines)


async def push_slack(text_: str) -> None:
    from app.services.slack_client import slack_post

    await slack_post("tiktok-trendwatch", text_)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--full", action="store_true", help="double the sweep with organicOnly=true")
    ap.add_argument("--slack", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    sweep = FULL_SWEEP if args.full else DEFAULT_SWEEP
    errors = []
    try:
        videos = fetch_trending_videos(sweep=sweep)
    except Exception as exc:  # noqa: BLE001 — report gap, don't fabricate
        videos = []
        errors.append(str(exc))

    rows = build_matrix(videos)
    scenarios = model_scenarios(rows)

    if args.json:
        print(json.dumps({"scenarios": scenarios, "errors": errors}, indent=2))
    else:
        print(render(scenarios, rows))
        for e in errors:
            print(f"feed error: {e}", file=sys.stderr)

    if args.slack and scenarios:
        asyncio.run(push_slack(render(scenarios, rows)))
    return 0 if not errors else 2


if __name__ == "__main__":
    raise SystemExit(main())

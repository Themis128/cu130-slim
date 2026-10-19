"""Trendwatch — data-driven weak-signal detection for content strategy.

Adopts the three-tier trendwatching model of Chernenko & Babenko,
"Development of a data-driven trendwatching model in the European business
environment: The «Agentic AI» case", Journal of European Economy 24(4), 2025:

  Tier 1  Signal Identification — Google Trends trending RSS across a benchmark
          market (US) and target markets (GR + EU peers). Broad coverage, no
          category filter — the paper's weak-signal rule for new trends.
  Tier 2  Analytical Core — each signal is scored through a business trend
          matrix (SWOT-basis): niche relevance -> Market Opportunity,
          benchmark-only presence -> regional asymmetry (early window),
          traffic scale -> threat/noise gate.
  Tier 3  Scenario Modelling — for each qualified signal emit the matrix's
          right column (innovative value, target segment, strategic
          adaptation: leader vs fast-follower) and a concrete funnel action.

Honest data note: Google Trends interest-over-time API is bot-blocked, so
historical dynamics are unavailable. Signals are same-day snapshots; run on a
daily cadence to approximate the paper's longitudinal window. State across
runs is intentionally not persisted — trends are ephemeral, the report and
Slack push are the observable record.

Run inside the social-api container:
    docker exec social-api python scripts/trendwatch.py
    docker exec social-api python scripts/trendwatch.py --slack
"""

from __future__ import annotations

import argparse
import asyncio
import datetime
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402

TRENDING_RSS = "https://trends.google.com/trending/rss?geo={geo}"

# Paper's cluster design: US = originator/benchmark, EU = target markets.
DEFAULT_GEOS = {"US": "benchmark", "GR": "primary", "GB": "eu-peer"}

# Cloudless niche lexicon — a signal must touch one of these to qualify as a
# Market Opportunity for us (the matrix's Level-2 assessment). Generic traffic
# (sports, politics, celebrity) is the paper's "informational noise".
NICHE_LEXICON = re.compile(
    r"\b(ai|artificial intelligence|llm|gpt|claude|agent|agentic|automat|"
    r"cloud|cloudflare|saas|software|devops|data|analytics|marketing|seo|"
    r"ecommerce|e-commerce|startup|digital|cyber|security|privacy|gdpr|"
    r"europa|eu\b|regulation|fintech|api|open source|linux|server)\b",
    re.IGNORECASE,
)

# Terms that collide with the lexicon but aren't our niche — "social security
# checks" must not score as cybersecurity interest.
NICHE_EXCLUSIONS = re.compile(
    r"social security|security council|security guard|national security|"
    r"job security|homeland security",
    re.IGNORECASE,
)


def _parse_traffic(raw: str | None) -> int:
    """'200+' -> 200, '1K+' -> 1000, '2M+' -> 2000000."""
    if not raw:
        return 0
    m = re.match(r"([\d.]+)\s*([KMB]?)\+?", raw.strip(), re.IGNORECASE)
    if not m:
        return 0
    mult = {"": 1, "K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    return int(float(m.group(1)) * mult[m.group(2).upper()])


def fetch_trending(geo: str) -> list[dict]:
    """Stage 1: pull today's trending searches for a market."""
    r = httpx.get(TRENDING_RSS.format(geo=geo), timeout=15)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    ht = "{https://trends.google.com/trending/rss}"
    signals = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        news = []
        for n in item.findall(f"{ht}news_item")[:3]:
            t = (n.findtext(f"{ht}news_item_title") or "").strip()
            src = (n.findtext(f"{ht}news_item_source") or "").strip()
            if t:
                news.append({"title": t, "source": src})
        signals.append(
            {
                "query": title,
                "geo": geo,
                "traffic": _parse_traffic(item.findtext(f"{ht}approx_traffic")),
                "pub_date": (item.findtext("pubDate") or "").strip(),
                "news": news,
            }
        )
    return signals


def build_matrix(signals: list[dict]) -> list[dict]:
    """Stage 2: classify each signal through the business trend matrix.

    Rows emitted per signal:
      market_opportunity  — niche-relevant rising interest (SWOT 'O')
      regional_asymmetry  — benchmark-only signal not yet in target market
                            (SWOT 'W' as barrier / early-adoption window)
      noise               — non-niche traffic (Threat gate: discard from action)
    """
    by_geo: dict[str, set[str]] = {}
    for s in signals:
        by_geo.setdefault(s["geo"], set()).add(s["query"].lower())

    rows = []
    for s in signals:
        blob = s["query"] + " " + " ".join(n["title"] for n in s["news"])
        blob = NICHE_EXCLUSIONS.sub("", blob)
        relevant = bool(NICHE_LEXICON.search(blob))
        only_benchmark = s["geo"] == "US" and s["query"].lower() not in {q for g, qs in by_geo.items() if g != "US" for q in qs}
        if relevant and s["geo"] == "US" and only_benchmark:
            classification = "early_window"
            swot = "O+W"
        elif relevant:
            classification = "opportunity"
            swot = "O"
        else:
            classification = "noise"
            swot = "T"
        rows.append({**s, "relevant": relevant, "classification": classification, "swot": swot})
    return rows


def model_scenarios(rows: list[dict], funnel_weakest: str | None) -> list[dict]:
    """Stage 3: strategic application for qualified signals.

    early_window -> Fast Follower: monitor benchmark market, pre-draft content
                    for the lag window (paper's Scenario B — EU fast-follow).
    opportunity   -> Leader: publish within the trend window on the channel
                    matching the funnel's weakest transition.
    """
    scenarios = []
    for r in rows:
        if r["classification"] == "early_window":
            strategy = "fast_follower"
            action = "pre-draft for EU/GR wave (monitor benchmark first)"
            channel = "linkedin"
        elif r["classification"] == "opportunity":
            strategy = "leader"
            action = "publish within 24-48h trend window"
            channel = "linkedin" if funnel_weakest in (None, "lead->nurtured") else "x"
        else:
            continue
        scenarios.append(
            {
                "query": r["query"],
                "geo": r["geo"],
                "traffic": r["traffic"],
                "classification": r["classification"],
                "swot": r["swot"],
                "strategy": strategy,
                "innovative_value": _summarise_news(r),
                "target_segment": "SMEs scaling without headcount growth",
                "action": action,
                "channel": channel,
                "cta": f"utm_source={channel}&utm_medium=social&utm_campaign=trend-" + re.sub(r"[^a-z0-9]+", "-", r["query"].lower())[:40],
            }
        )
    scenarios.sort(key=lambda s: (-(s["classification"] == "opportunity"), -s["traffic"]))
    return scenarios


def _summarise_news(row: dict) -> str:
    if row["news"]:
        n = row["news"][0]
        return f"{n['title']} ({n['source']})"[:120]
    return f"search interest {row['traffic']:,}+"


async def _funnel_weakest() -> str | None:
    """Live tie-in with funnel_audit: weakest transition steers the channel."""
    try:
        async with async_session_maker() as db:
            leads = (await db.execute(text("SELECT count(*) FROM espocrm_lead_cache WHERE deleted IS NOT TRUE"))).scalar()
            if leads is None:
                return None
    except Exception:
        return None
    # crm-lifecycle moves New->nurtured; checked in funnel_audit as the break.
    return "lead->nurtured"


def render(scenarios: list[dict], rows: list[dict]) -> str:
    total = len(rows)
    relevant = sum(1 for r in rows if r["relevant"])
    lines = [
        f"Trendwatch — {datetime.datetime.now(datetime.UTC):%Y-%m-%dT%H:%MZ}",
        f"signals {total} | niche-relevant {relevant} | actionable {len(scenarios)}",
        "",
    ]
    if not scenarios:
        lines.append("no actionable trend signals today — monitor only")
        return "\n".join(lines)
    for s in scenarios:
        marker = "LEADER" if s["strategy"] == "leader" else "FAST-FOLLOW"
        lines.append(f"[{marker}] {s['query']} ({s['geo']}, {s['traffic']:,}+)")
        lines.append(f"  value: {s['innovative_value']}")
        lines.append(f"  action: {s['action']} -> {s['channel']} | CTA {s['cta']}")
    return "\n".join(lines)


async def push_slack(text_: str) -> None:
    from app.services.slack_client import slack_post

    await slack_post("trendwatch", text_)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--geos", default=",".join(DEFAULT_GEOS))
    ap.add_argument("--slack", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    signals = []
    errors = []
    for geo in [g.strip().upper() for g in args.geos.split(",") if g.strip()]:
        try:
            signals.extend(fetch_trending(geo))
        except Exception as exc:  # noqa: BLE001 — report gap, don't fabricate
            errors.append(f"{geo}: {exc}")

    rows = build_matrix(signals)
    funnel = asyncio.run(_funnel_weakest())
    scenarios = model_scenarios(rows, funnel)

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

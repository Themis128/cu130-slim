"""TikTok Creative Center trending-videos feed.

Source: the new Creative Center Trends app's unsigned report endpoints
(ads.us.tiktok.com/CreativeOne/Report/*) — no login, no signing, browser
headers only. Verified live 2026-11-08 after the October-2026 deprecation
of the legacy signed creative_radar_api endpoints.

Hard platform limits (logged-out tier):
- Videos are US-only; other country codes return empty sets.
- `page`/`limit` are ignored — every call returns exactly 4 videos.
  Wider coverage comes from sweeping orderByMetric x contentLabelIDs x
  organicOnly (~36 calls for ~80 distinct videos a week).
- `totalCount: 100` in the response is a login upsell, not a real page
  count.
- Pace calls (~1 req / 1.5-2s); bursts above ~40 trigger TLS resets.

Normalized signal dicts match `scripts/trendwatch.fetch_trending` shape
(query/geo/traffic/news) so the existing niche matrix + scenarios apply.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

BASE = "https://ads.us.tiktok.com/CreativeOne/Report"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Origin": "https://ads.tiktok.com",
    "Referer": "https://ads.tiktok.com/",
    "agw-js-conv": "str",
}

# orderByMetric values verified to return data (4/5 return nothing).
ORDER_METRICS = {1: "views", 2: "engagement_rate", 3: "six_second_views"}

# Creative Center content labels — the five reported in the new Trends app
# plus "" for the unfiltered list.
CONTENT_LABELS = {
    "": "all",
    "11002": "Beauty & Care",
    "11003": "Fashion",
    "11010": "Food & Beverage",
    "11013": "Lifestyle & Leisure",
    "11015": "Technology & Finance",
}

DEFAULT_SWEEP: tuple[dict[str, Any], ...] = tuple(
    {"orderByMetric": m, "contentLabelIDs": lbl, "organicOnly": "false"}
    for m in ORDER_METRICS
    for lbl in CONTENT_LABELS
)

FULL_SWEEP: tuple[dict[str, Any], ...] = DEFAULT_SWEEP + tuple(
    {**c, "organicOnly": "true"} for c in DEFAULT_SWEEP
)

# Prompt-context tier — 3 calls, ~5s, up to 12 videos (one per metric,
# unfiltered labels). The report script uses the wider sweeps.
LIGHT_SWEEP: tuple[dict[str, Any], ...] = tuple(
    {"orderByMetric": m, "contentLabelIDs": "", "organicOnly": "false"}
    for m in ORDER_METRICS
)

CALL_PACE_SEC = 1.6


def _overview_week_end(client: httpx.Client) -> str:
    r = client.get(f"{BASE}/GetTopContentsOverview", headers=HEADERS, timeout=15)
    r.raise_for_status()
    data = r.json()
    ts = data.get("lastWeeklyEndTimestamp")
    if not ts:
        raise RuntimeError("GetTopContentsOverview returned no weekly timestamp")
    return str(ts)


def _int(v: Any) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def _normalize(item: dict, combo: dict[str, Any], week_end: str) -> dict[str, Any]:
    info = item.get("itemInfo") or {}
    author = item.get("itemAuthorInfo") or {}
    author_metrics = item.get("itemAuthorMetrics") or {}
    metrics = item.get("itemMetrics") or {}
    handle = author.get("handlerName") or ""
    item_id = info.get("itemID") or ""
    title = (info.get("title") or "").strip()
    views = _int(metrics.get("videoViews"))
    organic = _int(metrics.get("organicVideoViews"))
    return {
        "query": title or f"@{handle} video {item_id}",
        "geo": "TT-US",
        "traffic": views,
        "pub_date": week_end,
        "news": [],
        "source": "tiktok-creative-center",
        "tiktok": {
            "item_id": item_id,
            "url": f"https://www.tiktok.com/@{handle}/video/{item_id}" if handle and item_id else "",
            "author": handle,
            "author_followers": _int(author_metrics.get("followers")),
            "author_bio": author.get("bio") or "",
            "weekly_views": views,
            "weekly_organic_views": organic,
            "engagement_rate": metrics.get("engagementRate"),
            "six_second_vtr": metrics.get("sixSecondsVTR"),
            "label": CONTENT_LABELS.get(combo.get("contentLabelIDs", ""), "all"),
            "order_metric": ORDER_METRICS.get(combo.get("orderByMetric"), "?"),
            "organic_only": combo.get("organicOnly") == "true",
            "week_ending": week_end,
        },
    }


def fetch_trending_videos(
    sweep: tuple[dict[str, Any], ...] = DEFAULT_SWEEP,
    country: str = "US",
    client: httpx.Client | None = None,
    pace: float = CALL_PACE_SEC,
) -> list[dict[str, Any]]:
    """Pull the current week's trending videos via the param sweep.

    Each call yields 4 items; dedupe by itemID. `pace` throttles requests —
    TikTok resets TLS on bursts (observed upstream ~40 rapid calls).
    """
    own = client is None
    if own:
        client = httpx.Client(http2=False)
    try:
        week_end = _overview_week_end(client)
        seen: dict[str, dict[str, Any]] = {}
        last_call = 0.0
        for combo in sweep:
            wait = pace - (time.monotonic() - last_call)
            if wait > 0:
                time.sleep(wait)
            params = {
                "countryCode": country,
                "orderByMetric": combo["orderByMetric"],
                "contentLabelIDs": combo.get("contentLabelIDs", ""),
                "organicOnly": combo.get("organicOnly", "false"),
                "periodDimension": 3,  # weekly
                "periodEndTimestamp": week_end,
                "page": 1,
                "limit": 20,
            }
            last_call = time.monotonic()
            r = client.get(
                f"{BASE}/CreativeCenterGetTopContentsList",
                params=params,
                headers=HEADERS,
                timeout=20,
            )
            r.raise_for_status()
            body = r.json()
            base = body.get("BaseResp") or {}
            if base.get("StatusCode") != 0:
                raise RuntimeError(f"CreativeCenter status {base.get('StatusCode')}: {base.get('StatusMessage')}")
            for item in body.get("entityInfos") or []:
                item_id = (item.get("itemInfo") or {}).get("itemID")
                if item_id and item_id not in seen:
                    seen[item_id] = _normalize(item, combo, week_end)
                elif item_id:
                    # merge: same video surfaced under a second label/metric
                    tt = seen[item_id].get("tiktok", {})
                    extra = tt.get("also_ranked") or []
                    extra.append((tt and combo.get("orderByMetric"), combo.get("contentLabelIDs")))
                    tt["also_ranked"] = extra
                    seen[item_id]["tiktok"] = tt
        return list(seen.values())
    finally:
        if own:
            client.close()

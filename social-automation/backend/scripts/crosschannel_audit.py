#!/usr/bin/env python3
"""Cross-channel integration audit — the ADMAN paper's unification rule.

Adoption of Yulian Prakosos, Ukar & Astutik, "Strategic Significance:
Unveiling the Dominant Role of Social Media as a Primary Marketing Tool in
Digital Business Strategies" (ADMAN, vol. 2 no. 1, April 2024, pp. 363-370,
DOI 10.61100/adman.v2i1.137). The paper's central operational point: social
media must not be a standalone entity — integrate it with other marketing
channels for a consistent brand experience, and (its hardest challenge)
measure ROI through attribution, not vanity metrics.

Our standing rule descends directly from it: multi-channel campaigns share
ONE ``utm_campaign`` slug across social + email + ads — ``utm_source``/
``utm_medium`` carry the channel, ``utm_content`` the asset. Per-channel
slugs fragment campaign attribution.

Live checks (no heuristics):

  slug inventory   — distinct utm_campaign values arriving in
                     web_analytics_events + email_campaigns cta_url
  slug sharing     — which slugs appear on >1 channel (integration),
                     which are single-channel (fragmented reach)
  CTA coverage     — sent email campaigns with a UTM-tagged CTA;
                     published posts carrying an owned-domain link (all are
                     tagged at publish by app/services/utm.py)
  attribution loop — UTM events landing in web_analytics_events, 30d
  influencer lane  — the paper's collaboration lever: honest status check
                     on the partner/influencer surface

Run inside social-api (PYTHONPATH=/app) or the backend venv:

    python scripts/crosschannel_audit.py          # human report
    python scripts/crosschannel_audit.py --json   # machine output
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text  # noqa: E402

from app.db.session import async_session_maker  # noqa: E402

_UTM_RE = re.compile(r"[?&]utm_campaign=([^&\s]+)")


def _slug_from_url(url: str | None) -> str | None:
    if not url:
        return None
    m = _UTM_RE.search(url)
    if m:
        return m.group(1)
    try:
        return dict(parse_qsl(urlsplit(url).query)).get("utm_campaign")
    except ValueError:
        return None


def _slug_from_body(html: str | None) -> str | None:
    """First utm_campaign found anywhere in the rendered HTML — matches
    href querystrings including `&amp;`-encoded params."""
    if not html:
        return None
    m = _UTM_RE.search(html.replace("&amp;", "&"))
    return m.group(1) if m else None


async def _slug_inventory() -> dict:
    """Campaign slugs observed per channel — the integration unit."""
    social, email, other = {}, {}, {}
    async with async_session_maker() as db:
        # Slugs that actually produced site events, by utm_source channel
        rows = (
            await db.execute(
                text(
                    "SELECT payload->>'utm_source' AS src,"
                    "       payload->>'utm_campaign' AS camp, count(*)"
                    "  FROM web_analytics_events"
                    " WHERE payload ? 'utm_campaign'"
                    " GROUP BY 1, 2 ORDER BY 3 DESC"
                )
            )
        ).all()
        for src, camp, n in rows:
            social_platforms = (
                "linkedin", "facebook", "instagram", "threads", "twitter",
                "tiktok", "telegram", "whatsapp", "bluesky", "viber",
            )
            bucket = social if src in social_platforms else other
            bucket.setdefault(camp or "(none)", 0)
            bucket[camp or "(none)"] += n

        # Email-side slugs — a campaign is tagged when EITHER the structured
        # meta_data.cta_url OR any link inside html_body carries utm_campaign
        # (older campaigns tag links in-body without a cta_url meta).
        erows = (
            await db.execute(
                text(
                    "SELECT name, meta_data->>'cta_url' AS cta, html_body"
                    "  FROM email_campaigns"
                    " WHERE status IN ('sent','sending','draft')"
                    " ORDER BY created_at DESC LIMIT 200"
                )
            )
        ).all()
        email_ctas = []
        for name, cta, html in erows:
            slug = _slug_from_url(cta) or _slug_from_body(html)
            email_ctas.append({"name": name, "slug": slug, "tagged": bool(slug)})
            if slug:
                email[slug] = email.get(slug, 0) + 1

    return {
        "social_event_slugs": social,
        "email_cta_slugs": email,
        "other_event_slugs": other,
        "email_ctas": email_ctas,
    }


async def _post_cta_coverage() -> dict:
    """Published posts carrying an owned-domain CTA link — every one is
    UTM-tagged at publish by utm.py, so coverage = posts that offer a click."""
    async with async_session_maker() as db:
        total, with_link = (
            await db.execute(
                text(
                    "SELECT count(*),"
                    "       count(*) FILTER (WHERE content_text ~* 'cloudless\\.gr')"
                    "  FROM posts"
                    " WHERE status = 'published'"
                    "   AND published_at > now() - interval '30 days'"
                )
            )
        ).first()
    return {
        "posts_30d": total or 0,
        "with_cta_link": with_link or 0,
        "pct": round(100 * (with_link or 0) / total, 1) if total else None,
    }


async def _attribution_30d() -> int:
    async with async_session_maker() as db:
        return (
            await db.execute(
                text(
                    "SELECT count(*) FROM web_analytics_events"
                    " WHERE occurred_at > now() - interval '30 days'"
                    "   AND payload ? 'utm_campaign'"
                )
            )
        ).scalar() or 0


async def audit() -> dict:
    inv, posts, attributed = await asyncio.gather(
        _slug_inventory(), _post_cta_coverage(), _attribution_30d()
    )

    # The paper's integration test: does any slug span >1 channel?
    email_slugs = set(inv["email_cta_slugs"])
    social_slugs = set(inv["social_event_slugs"])
    shared = sorted(email_slugs & social_slugs)
    email_only = sorted(email_slugs - social_slugs)
    social_only = sorted(social_slugs - email_slugs)

    tagged = sum(1 for c in inv["email_ctas"] if c["tagged"])
    email_cta_pct = round(100 * tagged / len(inv["email_ctas"]), 1) if inv["email_ctas"] else None

    gaps = []
    if not shared and (email_slugs or social_slugs):
        gaps.append(
            "zero shared campaign slugs — email and social never reference the "
            "same utm_campaign (ADMAN integration rule: one slug per campaign)"
        )
    if email_cta_pct is not None and email_cta_pct < 90:
        gaps.append(f"email CTA tagging {email_cta_pct}% — campaigns without utm_campaign break attribution")
    if posts["posts_30d"] and (posts["pct"] or 0) < 50:
        gaps.append(f"only {posts['pct']}% of published posts carry an owned-domain CTA")
    if attributed == 0:
        gaps.append("attribution silent — zero UTM events in 30d; ROI is unmeasurable (paper's hardest challenge)")

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "slugs": {
            "shared_across_channels": shared,
            "email_only": email_only,
            "social_only": social_only,
            "other_sources": inv["other_event_slugs"],
        },
        "coverage": {
            "email_cta_tagged_pct": email_cta_pct,
            "posts_with_cta_pct": posts["pct"],
            "posts_30d": posts["posts_30d"],
        },
        "attribution_events_30d": attributed,
        "influencer_surface": "unbuilt — paper's collaboration lever has no tracking surface (backlogged)",
        "gaps": gaps,
    }


def render(r: dict) -> str:
    s = r["slugs"]
    lines = [
        f"Cross-channel audit — {r['generated_at'][:16]}Z",
        "",
        "Campaign slugs (utm_campaign)",
        "-" * 60,
        f"shared across channels : {', '.join(s['shared_across_channels']) or '—'}",
        f"email-only             : {', '.join(s['email_only']) or '—'}",
        f"social-only            : {', '.join(s['social_only']) or '—'}",
    ]
    if s["other_sources"]:
        lines.append(f"other event sources    : {s['other_sources']}")
    cov = r["coverage"]
    lines += [
        "",
        f"email CTA tagged : {cov['email_cta_tagged_pct']}%",
        f"posts with CTA  : {cov['posts_with_cta_pct']}% of {cov['posts_30d']} (30d)",
        f"UTM events      : {r['attribution_events_30d']} (30d)",
        f"influencer lane : {r['influencer_surface']}",
        "",
    ]
    if r["gaps"]:
        lines.append("gaps:")
        lines += [f"  • {g}" for g in r["gaps"]]
    else:
        lines.append("gaps: none")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    report = asyncio.run(audit())
    print(json.dumps(report, indent=2) if args.json else render(report))
    sys.exit(1 if report["gaps"] else 0)


if __name__ == "__main__":
    main()

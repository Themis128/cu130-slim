#!/usr/bin/env python3
"""Mint the full LTE (tracked-link) set for the funnel surfaces.

Generates every platform × surface UTM-tagged link via tracked_url —
the links that feed web_analytics_events → R2 datalake → DuckDB.

Run inside social-api / worker:  python /app/scripts/lte_links.py [--surface S] [--platform P]
"""

import argparse
import sys

sys.path.insert(0, "/app")
from app.services.utm import tracked_url

SURFACES = {
    "personal": "https://cloudless.gr/links",
    "audit": "https://cloudless.gr/contact",
    "pricing": "https://social.cloudless.gr/pricing",
}
PLATFORMS = ["linkedin", "x", "instagram", "threads", "facebook", "tiktok", "telegram"]


def main(a):
    surfs = {a.surface: SURFACES[a.surface]} if a.surface else SURFACES
    plats = [a.platform] if a.platform else PLATFORMS
    for name, url in surfs.items():
        for p in plats:
            print(tracked_url(url, source=p, medium="organic_social", campaign=f"{name}-funnel", content=a.content))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--surface", choices=list(SURFACES), default=None)
    ap.add_argument("--platform", default=None)
    ap.add_argument("--content", default="funnel-post")
    main(ap.parse_args())

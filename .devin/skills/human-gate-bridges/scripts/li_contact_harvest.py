#!/usr/bin/env python3
"""li_contact_harvest.py — post-December-2026 durable lane for LinkedIn
contact data. LinkedIn's data export drops Email Address / Company /
Position from Connections.csv, but each connection's profile still shows
whatever contact info they share with 1st-degree connections — which is a
DIFFERENT, far more permissive toggle than the export opt-in (verified
2026-11-25: emails visible in the overlay that were absent from the CSV).

For each connection slug: navigate the live linkedin-browser-sidecar
session to /in/<slug>/overlay/contact-info/ (renders server-side — the
URL alone loads the modal) and scrape Email / phone / websites + the
profile headline (company + role). Writes an enriched CSV audience_watch
can ingest.

Run inside social-api (docker cp first — repo isn't mounted):

    docker cp li_contact_harvest.py social-api:/tmp/
    docker exec social-api python3 /tmp/li_contact_harvest.py \
        --connections /tmp/Connections.csv --out /tmp/li_contacts.csv

Options: --limit N (default 120/run — LinkedIn profile-view throttling),
--delay LOW-HIGH seconds (default 5-9 jitter), --resume (skip slugs
already in --out).
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.request
from pathlib import Path

SIDECAR = os.environ.get("SIDECAR_URL", "http://linkedin-browser-sidecar:9225")
EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
SLUG = re.compile(r"linkedin\.com/in/([^/?#]+)")


def _post(path: str, body: dict, timeout: int = 60) -> dict:
    req = urllib.request.Request(
        SIDECAR + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as f:
            return json.loads(f.read())
    except Exception as e:  # noqa: BLE001 — a failed probe must not kill the batch
        return {"error": str(e)}


def ev(expr: str, timeout: int = 40):
    return _post("/debug/eval", {"expr": expr}, timeout).get("value")


def harvest_one(slug: str) -> dict:
    # /in/<slug>/overlay/contact-info/ only renders when reached by the
    # in-page "Contact info" link — direct navigation redirects to the
    # plain profile. Load the profile, click the link, read the modal.
    _post("/debug/navigate", {
        "url": f"https://www.linkedin.com/in/{slug}/",
        "waitUntil": "domcontentloaded"}, 60)
    time.sleep(4)
    clicked = ev(
        "return (function(){const a=[...document.querySelectorAll('a')]"
        ".find(e=>/overlay\\/contact-info/.test(e.href||''));"
        "if(!a)return 'no-link';a.click();return 'clicked'})()")
    if clicked != "clicked":
        return {}
    time.sleep(3)
    raw = ev(
        "return JSON.stringify({u:location.href,"
        "text:(document.body.innerText||'').slice(0,2500)})")
    if not raw:
        return {}
    try:
        page = json.loads(raw)
    except Exception:  # noqa: BLE001
        return {}
    text = page.get("text") or ""
    if "Contact info" not in text:
        return {}
    # Contact-info block sits before the premium upsell / nav chrome.
    block = text.split("Connected since")[0]
    emails = [e for e in EMAIL.findall(block)
              if not e.lower().endswith((".png", ".jpg", "linkedin.com"))]
    # headline = first line after the name on the underlying profile
    m = re.search(r"Skip to footer\s*\n\s*\n?([^\n]{3,80})\s*\n\s*\n?([^\n]{5,160})",
                  text)
    headline = (m.group(2).strip() if m else "")
    return {
        "emails": sorted(set(emails)),
        "headline": headline,
        "url": page.get("u", ""),
    }


def slugs_from_csv(path: Path) -> list[str]:
    out = []
    with path.open(errors="replace") as f:
        for row in csv.reader(f):
            for cell in row:
                m = SLUG.search(cell)
                if m:
                    out.append(m.group(1))
    return list(dict.fromkeys(out))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--connections", default="/tmp/Connections.csv")
    ap.add_argument("--out", default="/tmp/li_contacts.csv")
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--delay", default="5-9")
    ap.add_argument("--resume", action="store_true")
    a = ap.parse_args()

    lo, hi = (int(x) for x in a.delay.split("-", 1))
    slugs = slugs_from_csv(Path(a.connections))
    done = set()
    outp = Path(a.out)
    if a.resume and outp.exists():
        with outp.open(errors="replace") as f:
            for row in csv.reader(f):
                if row and row[0] in slugs:
                    done.add(row[0])
    todo = [s for s in slugs if s not in done][: a.limit]
    print(f"{len(slugs)} slugs, {len(done)} done, harvesting {len(todo)}")

    new = not outp.exists()
    with outp.open("a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["slug", "email", "headline", "profile_url"])
        got = 0
        for i, slug in enumerate(todo, 1):
            r = harvest_one(slug)
            mail = r.get("emails", [""])[0] if r.get("emails") else ""
            w.writerow([slug, mail, r.get("headline", ""), r.get("url", "")])
            f.flush()
            got += bool(mail)
            print(f"[{i}/{len(todo)}] {slug}: {'MAIL ' + mail if mail else '—'} "
                  f"({r.get('headline','')[:50]})", flush=True)
            time.sleep(random.uniform(lo, hi))
    print(f"done — {got} emails in {len(todo)} profiles; out={outp}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

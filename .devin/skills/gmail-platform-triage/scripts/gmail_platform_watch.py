#!/usr/bin/env python3
"""gmail_platform_watch.py — proactive platform-issue scanner for Gmail.

Runs the gmail-platform-triage auth + search lanes on a schedule and alerts
(via tg_alert.py -> Telegram #ops) ONLY when platform mail matches an
issue keyword. Born from the 2026-11-26 incident: the Instagram "we locked
your profile" email sat unread for ~12h until a manual scan.

Coverage: security categories (linkedin-sec, google-sec) plus high-signal
platform senders (meta, x, tiktok-app, whatsapp, viber, telegram,
cloudflare, vercel, polar, resend). Read-only token is enough.

State: ~/.cache/gmail_platform_watch/seen.json dedupes alerted message ids
(30d TTL). Cron-safe: always exits 0, prints a one-line summary.

    python3 gmail_platform_watch.py            # scan + alert on hits
    python3 gmail_platform_watch.py --dry-run  # print hits, send nothing
    python3 gmail_platform_watch.py --hours 6  # window override (default 3)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import gmail_platform_triage as GPT  # noqa: E402

STATE_DIR = Path.home() / ".cache" / "gmail_platform_watch"
STATE_FILE = STATE_DIR / "seen.json"
TG_ALERT = (
    Path(__file__).resolve().parents[2]
    / "telegram-alert-ops" / "scripts" / "tg_alert.py"
)

# Categories worth watching. Security cats are always included; the rest are
# senders that emit account-actionable mail (locks, suspensions, webhook
# blocks, policy strikes, payout/verification issues).
WATCH_CATS = [
    "meta", "linkedin", "linkedin-sec", "tiktok-app", "x", "whatsapp",
    "viber", "telegram", "cloudflare", "vercel", "polar", "resend",
    "google-sec", "google-dev",
]

# Subject/from keywords that turn platform mail into an ops issue.
# Deliberately excludes routine auth noise (verify codes, password-changed
# confirmations, new-login alerts) — those are heal-lane business, not
# incidents. Only account-actionable events alert.
ISSUE_RE = GPT.re.compile(
    r"(locked|suspend|blocked|checkpoint|disabled|policy|violation|"
    r"unusual activity|security alert|webhook|restricted|appeal|"
    r"terminat|breach|unauthori[sz]ed|recovered your|account recovery|"
    r"will be (deleted|removed|disabled)|permanently)",
    GPT.re.I,
)

# Senders whose mail is pure noise even when it matches a keyword
# (newsletters, marketing). Kept tiny — err toward alerting.
NOISE_FROM = GPT.re.compile(
    r"(newsletter|marketing|promo|updates@|news@|digest|no-?reply@.*\.(shop|store))",
    GPT.re.I,
)


def _load_seen() -> dict:
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {}


def _save_seen(seen: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - 30 * 86400
    seen = {k: v for k, v in seen.items() if v > cutoff}
    STATE_FILE.write_text(json.dumps(seen))


def _alert(text: str, dry: bool) -> None:
    if dry:
        print(f"[dry-run] would alert: {text[:120]}")
        return
    try:
        subprocess.run(
            [sys.executable, str(TG_ALERT), "ops", text],
            timeout=30, check=False, capture_output=True,
        )
    except Exception as e:  # noqa: BLE001 — alerting must never kill the scan
        print(f"alert send failed: {e}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=int, default=3, help="look-back window")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    days = max(1, (args.hours + 23) // 24)
    env = GPT._load_env()
    at, _can_mod = GPT._access_token(env)
    me = GPT._api(at, "profile").get("emailAddress", "?")

    seen = _load_seen()
    hits: list[str] = []
    scanned = 0

    for name in WATCH_CATS:
        cat = GPT.CATEGORIES.get(name)
        if not cat:
            continue
        q = f"{cat['q']} newer_than:{days}d"
        try:
            msgs = GPT._search(at, q, 50)
        except Exception as e:  # noqa: BLE001
            print(f"{name}: search error {str(e)[:60]}")
            continue
        for m in msgs:
            mid = m["id"]
            if mid in seen:
                continue
            scanned += 1
            try:
                f = GPT._api(
                    at,
                    f"messages/{mid}?format=metadata"
                    "&metadataHeaders=From&metadataHeaders=Subject&metadataHeaders=Date",
                )
            except Exception:  # noqa: BLE001
                continue
            sender = GPT._hdr(f, "From")
            subject = GPT._hdr(f, "Subject")
            date = GPT._hdr(f, "Date")[:16]
            hay = f"{sender} {subject}"
            if not ISSUE_RE.search(hay) or NOISE_FROM.search(sender):
                seen[mid] = time.time()
                continue
            hits.append(f"[{name}] {date} | {sender[:45]} | {subject[:80]}")
            seen[mid] = time.time()

    _save_seen(seen)

    if hits:
        body = "\n".join(hits[:10])
        _alert(
            f"gmail-platform-watch: {len(hits)} platform issue(s) in {me}\n{body}",
            args.dry_run,
        )
    print(
        f"gmail-platform-watch {me}: {scanned} new msgs scanned, "
        f"{len(hits)} issue hit(s)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

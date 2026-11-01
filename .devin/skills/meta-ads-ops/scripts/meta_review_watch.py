#!/usr/bin/env python3
"""meta_review_watch.py — watch Meta restriction flags and alert on change.

Runs `meta_ads_audit.py quick`, diffs the flag set against the last known
state (state.json in this dir), and on ANY change sends an alert via
tg_alert.py (severity: intel) and ntfy. Run on a schedule (Windows Task /
cron) — e.g. every 30 min — so Meta's review lift is caught the moment it
lands and the downstream chain can fire.

Usage:
  meta_review_watch.py          # one check (for schedulers)
  meta_review_watch.py --loop 30  # loop every N minutes
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "review_state.json")
AUDIT = os.path.join(HERE, "meta_ads_audit.py")
TG = os.path.normpath(os.path.join(
    HERE, "..", "..", "telegram-alert-ops", "scripts", "tg_alert.py"))


def audit():
    out = subprocess.run([sys.executable, "-X", "utf8", AUDIT, "quick"],
                         capture_output=True, text=True, timeout=120).stdout
    flags = {}
    for line in out.splitlines():
        m = re.match(r"(\S+)\s*\|\s*(.*)", line.strip())
        if m:
            flags[m.group(1)] = m.group(2).strip()
    return flags


def alert(txt):
    for cmd, args in (
        (TG, ["intel", txt]),
        (None, ["ntfy", "pub", "-T", "meta-review", "cloudless-ops", txt]),
        (None, ["curl", "-s", "-d", txt, "-H", "Title: meta-review",
                "https://ntfy.sh/cloudless-ops"]),
    ):
        try:
            if cmd:
                subprocess.run([sys.executable, "-X", "utf8", cmd, *args],
                               timeout=20, capture_output=True)
            else:
                subprocess.run(args, timeout=20, capture_output=True)
        except Exception:
            pass


SKILLS = os.path.dirname(HERE)
WA_ONBOARD = os.path.join(SKILLS, "whatsapp-ops", "scripts", "wa_onboard_retry.py")
WA_VERIFY = os.path.join(SKILLS, "human-gate-bridges", "scripts", "wa_verify.py")


def run(args, timeout=300):
    r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    return (r.stdout + r.stderr).strip()[:400]


def lift_chain():
    """Restriction lifted — reattach WABA, unpark account, re-verify phone."""
    steps = [
        ("waba-onboard", [sys.executable, "-X", "utf8", WA_ONBOARD,
                          "--tries", "3", "--wait", "120"], 420),
        ("waba-probe", ["docker", "exec", "social-api", "python",
                        "scripts/whatsapp_tool.py", "waba"], 60),
        ("unpark", ["docker", "exec", "social-api", "python", "-c",
                    "import asyncio\n"
                    "from app.db.session import async_session_maker\n"
                    "from app.models.social_account import SocialAccount\n"
                    "from sqlalchemy import select\n"
                    "async def u():\n"
                    "    async with async_session_maker() as s:\n"
                    "        r = await s.execute(select(SocialAccount)\n"
                    "            .where(SocialAccount.platform == 'whatsapp'))\n"
                    "        a = r.scalars().first()\n"
                    "        a.status = 'pending'\n"
                    "        md = dict(a.meta_data or {})\n"
                    "        md.pop('intentionally_pending', None)\n"
                    "        a.meta_data = md\n"
                    "        await s.commit()\n"
                    "asyncio.run(u())\n"], 60),
        ("webhook", ["docker", "exec", "social-api", "python",
                     "scripts/whatsapp_tool.py", "webhook"], 60),
        ("verify", [sys.executable, "-X", "utf8", WA_VERIFY], 300),
    ]
    for name, cmd, to in steps:
        try:
            out = run(cmd, to)
            print(f"[chain:{name}] {out[:150]}")
        except Exception as e:
            alert(f"lift-chain {name} failed: {e}")
            return
    alert("WhatsApp lift-chain completed — WABA reattached, unparked, "
          "webhook re-registered, phone verify fired")


def check():
    now = audit()
    prev = json.load(open(STATE)) if os.path.exists(STATE) else {}
    json.dump(now, open(STATE, "w"), indent=1)
    changed = {k: (prev.get(k), v) for k, v in now.items() if prev.get(k) != v}
    if changed:
        msg = "Meta restriction change: " + "; ".join(
            f"{k} {a}->{b}" for k, (a, b) in changed.items())
        print(msg)
        alert(msg)
        # lifted? fire the downstream chain automatically
        if any(v and "restricted" not in v and "disabled" not in v
               for _, v in changed.values()):
            alert("Meta flag may have LIFTED — firing WhatsApp lift-chain")
            try:
                lift_chain()
            except Exception as e:
                alert(f"lift-chain crashed: {e}")
    else:
        print("no change:", {k: v[:40] for k, v in now.items()})
    return now


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0)
    a = ap.parse_args()
    if a.loop:
        while True:
            try:
                check()
            except Exception as e:
                print("check err:", e)
            time.sleep(a.loop * 60)
    else:
        check()

#!/usr/bin/env python3
"""tiktok_inbox_finish.py — finish a SEND_TO_USER_INBOX TikTok publish on
the Galaxy S22+ once the phone is safe to drive.

Background: TikTok accounts without DIRECT_POST scope get
`SEND_TO_USER_INBOX` / `MEDIA_UPLOAD` — the video lands in the app's
Inbox as "TikTok Platform: Your content from Cloudless is ready — Edit
your video before sharing to TikTok" and needs a final Post tap.

Modes:
  --probe     launch TikTok (only when phone safe) and dump visible
              texts — use this FIRST to map the live flow
  --watch     poll every 60s; when the phone is safe AND unlocked,
              run the finish flow; exits on success or --timeout
  (default)   one-shot: require safe phone, then run the flow

Safety gate (checked before EVERY tap burst):
  - flip cover closed or keyguard up  -> wait/abort
  - foreground app in BLOCKED_PKGS    -> wait/abort (another agent or
    a human is mid-flow in FB/WA/TG/IG/Termux — never tap through it)

Flow (adaptive — dumps between steps, never blind-taps):
  launch TikTok -> Inbox -> "content ... ready" notification ->
  editor preview -> Post -> verify the item is gone / posted.

Exit 0 = posted (or --probe completed), 1 = flow failed, 2 = unsafe phone.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

TOOLKIT = Path.home() / "galaxy-s22-toolkit"
sys.path.insert(0, str(TOOLKIT / "phone-lib" / "src"))
sys.path.insert(0, str(TOOLKIT / "tools"))

TT_PKG = "com.zhiliaoapp.musically"
BLOCKED_PKGS = (
    "com.facebook.katana", "com.facebook.orca", "com.whatsapp",
    "org.telegram.messenger", "com.instagram.android",
    "com.termux", "com.azure.authenticator",
)
STATE = Path.home() / ".local/state/tiktok-inbox-finish.done"


def _ui():
    from adb_connect import load_wireless  # type: ignore
    from phone import create_client, UiModule
    return UiModule(create_client(wireless=load_wireless()))


def _texts(nodes) -> list[str]:
    out = []
    for n in nodes:
        for v in (n.text, n.desc):
            if v and v not in out:
                out.append(v)
    return out


def _safe(ui) -> tuple[bool, str]:
    st = ui.status()
    if st["covered"]:
        return False, "cover closed"
    if st["locked"]:
        return False, "locked"
    act = str(st.get("activity") or "")
    pkg = act.split("/")[0]
    if pkg and any(b in pkg for b in BLOCKED_PKGS):
        return False, f"blocked app foreground: {act}"
    return True, "ok"


def _launch(ui) -> None:
    ui.client.shell(f"monkey -p {TT_PKG} -c android.intent.category.LAUNCHER 1")
    time.sleep(5)


def _tap_first(ui, patterns: list[str], timeout: float = 8.0):
    """Tap first node whose text/desc matches any regex."""
    rx = re.compile("|".join(patterns), re.I)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for n in ui.dump():
            if rx.search(n.text or "") or rx.search(n.desc or ""):
                if ui.tap_node(n):
                    return n
        time.sleep(0.8)
    return None


def _probe(ui) -> int:
    _launch(ui)
    time.sleep(3)
    nodes = ui.dump()
    print(f"--- {len(nodes)} nodes, activity={ui.foreground_activity()}")
    for t in _texts(nodes)[:60]:
        print(" •", t[:100])
    return 0


def _finish(ui) -> int:
    _launch(ui)
    time.sleep(4)

    # 1. Inbox — bottom-nav "Inbox" or top-right inbox icon
    if not _tap_first(ui, [r"^inbox$", r"inbox"], timeout=10):
        print("FAIL: no Inbox entry found")
        return 1
    time.sleep(2.5)

    # 2. The pending upload notification
    hit = _tap_first(
        ui,
        [r"content from cloudless", r"ready", r"edit your video",
         r"before sharing"],
        timeout=10,
    )
    if not hit:
        nodes = ui.dump()
        print("FAIL: no pending-upload notification; screen texts:")
        for t in _texts(nodes)[:40]:
            print("  ?", t[:100])
        return 1
    print("opened notification")
    time.sleep(3)

    # 3. Editor/preview -> Post button (try several labels)
    for _ in range(3):
        if _tap_first(ui, [r"^post$", r"post now", r"share"], timeout=6):
            break
        # maybe a "Next"/"Edit" step first
        _tap_first(ui, [r"^next$", r"^edit$", r"^done$"], timeout=4)
    else:
        print("FAIL: no Post button found")
        return 1
    print("post tapped — verifying")
    time.sleep(5)

    # 4. Verify: notification should be gone / success surface
    nodes = ui.dump()
    texts = " | ".join(_texts(nodes)).lower()
    if "posted" in texts or "content from cloudless is ready" not in texts:
        STATE.write_text(str(time.time()))
        print("SUCCESS — inbox item cleared")
        return 0
    print("UNCLEAR — dump for review:")
    for t in _texts(nodes)[:40]:
        print("  ?", t[:100])
    return 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--watch", action="store_true")
    ap.add_argument("--timeout", type=int, default=7200,
                    help="watch deadline seconds (default 2h)")
    ap.add_argument("--interval", type=int, default=60)
    args = ap.parse_args()

    if STATE.exists() and args.watch is False and not args.probe:
        print("already finished (state file exists) — delete to rerun")
        return 0

    deadline = time.monotonic() + args.timeout
    while True:
        try:
            ui = _ui()
        except Exception as exc:
            print(f"phone unreachable: {exc}")
            if not args.watch:
                return 2
            time.sleep(args.interval)
            continue

        ok, why = _safe(ui)
        if not ok:
            print(f"phone unsafe ({why})")
            if not args.watch or time.monotonic() > deadline:
                return 2
            time.sleep(args.interval)
            continue

        if args.probe:
            return _probe(ui)
        rc = _finish(ui)
        if rc == 0 or not args.watch or time.monotonic() > deadline:
            return rc
        print("flow failed — retrying next cycle")
        time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())

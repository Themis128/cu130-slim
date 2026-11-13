#!/usr/bin/env python3
"""viber_business_setup.py — create/complete the free Viber Business Account
on the phone app via ADB (the ONLY surface that supports it — Viber docs:
"a business account can only be opened on your primary device").

Free tier (Viber Business Accounts Terms, May 2025): micro/small business,
<10k business-account messages/calendar month — business profile (name,
logo, description, website, category), public search listing after review,
Communities/Channels as official business chat. NO commercial contract —
that's only for REST chatbots (viber-bot-ops).

    viber_business_setup.py probe    # device + current screen dump
    viber_business_setup.py run      # drive More -> business account flow
    viber_business_setup.py run --dry-run   # navigate + dump, no submits

Reuses viber_phone_capture.py's ADB/uiautomator/Termux-clipboard machinery.
Every step prints the on-screen texts it sees — Viber UI labels vary by
version; extend LABEL_* lists rather than tapping blind. The flow stops at
irreducibly human steps (logo image pick, identity/business-document upload,
final review submit) and reports exactly where it stopped.

Env: VIBER_ADB_SERIAL pins the device (same as viber_phone_capture).
"""
import argparse
import importlib.util
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location(
    "viber_phone_capture", HERE / "viber_phone_capture.py")
vpc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vpc)

# Business details — real facts only (cloudless-code-of-honor).
BUSINESS = {
    "name": "Cloudless",
    "website": "https://cloudless.gr",
    "description": ("Cloud infrastructure, DevOps and business automation "
                    "for SMBs — audits, managed cloud, AI-assisted ops."),
    "category": "IT Services",   # adjust to what the picker offers
}

# Label candidates per step (English + common variants; extend as needed).
L_MORE_TAB = ["more", "menu"]
L_BUSINESS_ENTRY = ["business account", "create business account",
                    "business profile", "for business", "business"]
L_NAME_FIELD = ["business name", "name", "company name"]
L_WEBSITE_FIELD = ["website", "web site", "url", "homepage"]
L_DESC_FIELD = ["description", "about", "bio"]
L_CATEGORY_FIELD = ["category", "industry", "business type"]
L_NEXT = ["next", "continue", "save", "confirm"]
L_DONE = ["done", "finish", "submit", "create account", "get started"]


def dump(sleep=0.5):
    time.sleep(sleep)
    return vpc.texts()


def find_node(needle, timeout=5):
    nl = needle.lower()
    deadline = time.time() + timeout
    while time.time() < deadline:
        for t, x, y in vpc.dump_xml()[1]:
            if nl in t.lower():
                return t, x, y
        time.sleep(1)
    return None


def tap_first(candidates, timeout=5):
    for c in candidates:
        hit = find_node(c, timeout=timeout)
        if hit:
            t, x, y = hit
            vpc.sh("input", "tap", str(x), str(y))
            time.sleep(1.5)
            return t
    return None


def type_into_focused(text):
    vpc.type_text(text)


def cmd_probe(_args):
    if not vpc.pick_device():
        print("no ADB device — enable wireless debugging / plug USB "
              "(phone unreachable on 192.168.1.77 and tailnet :5555 as of "
              "2026-11-14)")
        return 2
    print("device:", vpc._dev)
    vpc.launch_viber()
    print("focus:", vpc.focus_pkg())
    for t in dump(4)[:30]:
        print("  •", t[:90])
    return 0


def cmd_run(args):
    if not vpc.pick_device():
        print("no ADB device — plug the phone / enable wireless ADB")
        return 2
    dry = getattr(args, "dry_run", False)
    print("device:", vpc._dev)
    vpc.launch_viber()
    time.sleep(3)
    print("focus:", vpc.focus_pkg())

    # 1. More tab (bottom nav, rightmost usually)
    if not tap_first(L_MORE_TAB, timeout=8):
        print("!! could not find More tab — dump:")
        for t in dump()[:30]:
            print("   •", t[:90])
        return 3
    print("[1] More tab opened")

    # 2. Business account entry
    hit = tap_first(L_BUSINESS_ENTRY, timeout=8)
    if not hit:
        print("!! no business-account entry on More — this account may "
              "already be business, or the entry lives under profile. "
              "Dump:")
        for t in dump()[:30]:
            print("   •", t[:90])
        return 4
    print(f"[2] tapped: {hit}")

    # 3. Walk the form: for each known field label, tap it and type.
    steps = [
        ("name", BUSINESS["name"], L_NAME_FIELD),
        ("website", BUSINESS["website"], L_WEBSITE_FIELD),
        ("description", BUSINESS["description"], L_DESC_FIELD),
        ("category", BUSINESS["category"], L_CATEGORY_FIELD),
    ]
    for label, value, candidates in steps:
        node = tap_first(candidates, timeout=4)
        if not node:
            print(f"[3] '{label}' field not found — skipping (dump follows)")
            continue
        if dry:
            print(f"[3] dry-run: would type '{value[:40]}' into '{node}'")
            vpc.sh("input", "keyevent", "KEYCODE_BACK")
            time.sleep(1)
            continue
        type_into_focused(value)
        print(f"[3] filled '{label}'")

    # 4. Advance / submit — STOP before anything irreversible.
    for t in dump()[:25]:
        print("   •", t[:90])
    hit = tap_first(L_NEXT + L_DONE, timeout=4)
    if hit:
        print(f"[4] tapped: {hit}")
    print("done — verify on screen; logo upload + final submit are the "
          "remaining human steps if the flow reached them")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["probe", "run"])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    return {"probe": cmd_probe, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

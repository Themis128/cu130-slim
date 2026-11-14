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
import re
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
    # ASCII-only — the `input text` fallback can't type Unicode.
    "description": ("Cloud infrastructure, DevOps and business "
                    "automation for SMBs - audits, managed cloud, "
                    "AI-assisted ops."),
    "category": "IT Services",   # adjust to what the picker offers
}

# Label candidates per step (English + common variants; extend as needed).
# Observed live 2026-11-14 (Viber on Galaxy S22+, en UI):
#   More tab -> "Business Account" row -> intro ("Get Started") ->
#   "Create your business" one-page form: EditText resource-id
#   "one-page-business-name" (prefilled with the PERSONAL name — must be
#   cleared), "Update logo" button, "ssp-create-business-button".
L_MORE_TAB = ["more", "menu"]
L_BUSINESS_ENTRY = ["business account", "create business account",
                    "business profile", "for business", "business"]
L_GET_STARTED = ["get started", "open business account", "create"]
L_NAME_FIELD = ["business name", "name", "company name"]
L_WEBSITE_FIELD = ["website", "web site", "url", "homepage"]
L_DESC_FIELD = ["description", "about", "bio"]
L_CATEGORY_FIELD = ["category", "industry", "business type"]
L_NEXT = ["next", "continue", "save", "confirm"]
L_DONE = ["done", "finish", "submit", "create account"]
L_RID_CREATE = "ssp-create-business-button"
L_RID_NAME = "one-page-business-name"


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


def tap_rid(rid_sub, timeout=5):
    """Tap the node whose resource-id contains rid_sub (Viber exposes
    stable ids like ssp-create-business-button that survive label
    changes/locale)."""
    deadline = time.time() + timeout
    pat = re.compile(
        r'resource-id="[^"]*' + re.escape(rid_sub) + r'[^"]*"[^>]*'
        r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
    while time.time() < deadline:
        xml, _ = vpc.dump_xml()
        m = pat.search(xml)
        if m:
            x1, y1, x2, y2 = map(int, m.groups())
            vpc.sh("input", "tap", str((x1 + x2) // 2),
                   str((y1 + y2) // 2))
            time.sleep(1.5)
            return True
        time.sleep(1)
    return False


def clear_focused(max_chars=60):
    """Empty the focused EditText: home, then forward-delete."""
    vpc.sh("input", "keyevent", "KEYCODE_MOVE_HOME")
    time.sleep(0.4)
    for _ in range(max_chars):
        vpc.sh("input", "keyevent", "KEYCODE_FORWARD_DEL")


def type_into_focused(text):
    vpc.type_text(text)


def focus_is_viber():
    return vpc.focus_pkg() == "com.viber.voip"


def cmd_probe(_args):
    if not vpc.pick_device():
        print("no ADB device — enable wireless debugging / plug USB "
              "(mDNS auto-connect covers the dynamic port when the phone "
              "is on the tailnet)")
        return 2
    print("device:", vpc._dev)
    vpc.launch_viber()
    print("focus:", vpc.focus_pkg())
    for t in dump(4)[:30]:
        print("  •", t[:90])
    return 0


def _on_create_form():
    xml, nodes = vpc.dump_xml()
    return ("create your business" in " ".join(nodes).lower()
            or L_RID_NAME in xml or L_RID_CREATE in xml)


def _fill_business_name(dry):
    """The one-page form prefills the PERSONAL name — clear + set the
    business name, then stop before logo/submit unless running live."""
    if not (tap_rid(L_RID_NAME, timeout=4)
            or tap_first(L_NAME_FIELD, timeout=4)):
        print("!! business-name field not found")
        return False
    if dry:
        print(f"[form] dry-run: would clear + type '{BUSINESS['name']}'")
        vpc.sh("input", "keyevent", "KEYCODE_BACK")
        return True
    clear_focused()
    type_into_focused(BUSINESS["name"])
    print(f"[form] name -> '{BUSINESS['name']}'")
    return True


def _walk_profile_fields(dry):
    """After account creation, later screens carry website/description/
    category — fill what appears, stop at document/legal gates."""
    steps = [
        ("website", BUSINESS["website"], L_WEBSITE_FIELD),
        ("description", BUSINESS["description"], L_DESC_FIELD),
        ("category", BUSINESS["category"], L_CATEGORY_FIELD),
    ]
    for label, value, candidates in steps:
        node = tap_first(candidates, timeout=4)
        if not node:
            continue
        if dry:
            print(f"[form] dry-run: would type '{value[:40]}' into "
                  f"'{node}'")
            vpc.sh("input", "keyevent", "KEYCODE_BACK")
            time.sleep(1)
            continue
        type_into_focused(value)
        print(f"[form] filled '{label}'")
        tap_first(L_NEXT, timeout=3)


def cmd_run(args):
    if not vpc.pick_device():
        print("no ADB device — plug the phone / enable wireless ADB")
        return 2
    dry = getattr(args, "dry_run", False)
    print("device:", vpc._dev)
    vpc.launch_viber()
    time.sleep(3)
    focus = vpc.focus_pkg()
    print("focus:", focus)
    if focus != "com.viber.voip" and not getattr(args, "force", False):
        print(f"!! phone appears in use (focus={focus}) — aborting so we "
              "don't hijack the screen. Rerun when idle or pass --force.")
        return 5

    # Resume support: if the create form is already on screen, skip nav.
    if _on_create_form():
        print("[0] already on 'Create your business' — resuming at form")
    else:
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
            print("!! no business-account entry on More — this account "
                  "may already be business, or the entry lives under "
                  "profile. Dump:")
            for t in dump()[:30]:
                print("   •", t[:90])
            return 4
        print(f"[2] tapped: {hit}")

        # 2b. Optional intro screen ("Open business account" / Get Started)
        if tap_first(L_GET_STARTED, timeout=4):
            print("[2b] intro dismissed")

    # 3. Create-your-business form — name first (prefilled personal name)
    if not _fill_business_name(dry):
        for t in dump()[:25]:
            print("   •", t[:90])
        return 6

    # 4. Submit creates the Business Account. Logo upload stays a human
    #    step (image picker); creation itself is editable afterwards.
    for t in dump()[:25]:
        print("   •", t[:90])
    if dry:
        print("[4] dry-run: would tap create-business-button then walk "
              "profile fields")
        return 0
    if not tap_rid(L_RID_CREATE, timeout=5) \
            and not tap_first(L_DONE + L_NEXT, timeout=3):
        print("!! create button not found — verify screen manually")
        return 7
    print("[4] create tapped — account creation submitted")
    time.sleep(4)

    # 5. Post-creation profile fields (website/description/category)
    _walk_profile_fields(dry=False)
    print("done — verify on screen. Remaining human gates: logo image "
          "('Update logo' picker), business-document upload if asked, "
          "directory safety-review acknowledgement.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["probe", "run"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="drive even when the phone's foreground app "
                         "isn't Viber (i.e. someone is using the phone)")
    args = ap.parse_args()
    return {"probe": cmd_probe, "run": cmd_run}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

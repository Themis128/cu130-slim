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
    viber_business_setup.py run      # create form OR complete profile
    viber_business_setup.py link     # open Business tools -> Chat link
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
L_SAVE = ["save"]
L_RID_CREATE = "ssp-create-business-button"
L_RID_NAME = "one-page-business-name"

# Post-creation surfaces (observed live 2026-11-14):
#   More tab -> "Business Account / Finish adding your details / Continue"
#   -> "Business tools" dashboard ("Manage profile", "Complete account",
#   "Add a Category", "Catalog", "Chat link", insights) -> "Manage
#   profile" -> "Edit profile" rows screen (Business Name / Category /
#   Business hours / Address / Description / Phone Number, each row with
#   an Add|Edit button at the right edge).
L_CONTINUE = ["continue", "finish adding your details"]
L_BUSINESS_DASH = ["business tools"]
L_MANAGE_PROFILE = ["manage profile"]
L_EDIT_PROFILE = ["edit profile"]
L_CHAT_LINK = ["chat link"]

# Category picker cells observed: IT, Consulting, Electronics, Software-
# adjacent labels. Exact-match first, substring fallback.
CATEGORY_CANDIDATES = ["it services", "it", "software", "technology",
                       "consulting", "electronics"]


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


def _nodes():
    """Full node list with bounds — dump_xml() drops bounds, and row
    actions (Add/Edit buttons) must be matched by y-position."""
    xml, _ = vpc.dump_xml()
    out = []
    for m in re.finditer(r"<node([^>]*)>", xml):
        attrs = m.group(1)

        def attr(name):
            mm = re.search(name + r'="([^"]*)"', attrs)
            return mm.group(1) if mm else ""

        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', attrs)
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        out.append({
            "text": attr("text") or attr("content-desc"),
            "cx": (x1 + x2) // 2, "cy": (y1 + y2) // 2,
            "x1": x1, "y1": y1, "x2": x2, "y2": y2,
            "clickable": attr("clickable") == "true",
            "rid": attr("resource-id"),
            "class": attr("class"),
        })
    return out


def _screen_has(*needles):
    joined = " ".join(vpc.texts()).lower()
    return all(n in joined for n in needles)


def _on_business_dashboard():
    return (_screen_has(*L_BUSINESS_DASH)
            or _screen_has("manage profile", "complete account"))


def _on_edit_profile():
    return (_screen_has(*L_EDIT_PROFILE)
            and _screen_has("business name"))


def _tap_xy(node):
    vpc.sh("input", "tap", str(node["cx"]), str(node["cy"]))
    time.sleep(1.5)


def _tap_row_action(row_label, actions=("add", "edit"), y_tol=80):
    """Edit-profile rows pair a label (left) with an Add/Edit button
    (right edge, same band). Tap the action nearest the label's y."""
    nl = row_label.lower()
    labels = [n for n in _nodes()
              if nl in n["text"].lower() and n["y1"] > 250]
    if not labels:
        return False
    ly = labels[0]["cy"]
    cands = [n for n in _nodes()
             if n["clickable"] and n["text"].strip().lower() in actions
             and abs(n["cy"] - ly) <= y_tol]
    if not cands:
        return False
    _tap_xy(min(cands, key=lambda n: abs(n["cy"] - ly)))
    return True


def _save_editor(dry, editor_titles):
    """Hide the IME before tapping Save — while the keyboard is open,
    bottom-edge taps land on IME keys (live-verified: stray chars and
    an emoji panel swallowed the Save tap). BACK once hides the IME but
    can also exit the editor; verify the editor title is still present
    before tapping Save."""
    if dry:
        print("[profile] dry-run: would dismiss IME + tap Save")
        return True
    vpc.sh("input", "keyevent", "KEYCODE_BACK")
    time.sleep(1.2)
    texts = [t.lower() for t in vpc.texts()]
    if not any(any(et in t for et in editor_titles) for t in texts):
        print("!! editor closed without saving (BACK exited) — "
              "field NOT saved; reopen and retry")
        return False
    if not tap_first(L_SAVE, timeout=4):
        print("!! Save button not found")
        return False
    time.sleep(2)
    return True


def _fill_text_editor(dry, text, editor_titles):
    """Focus the EditText, type, then save — shared by description-style
    single-field editors."""
    ets = [n for n in _nodes() if "EditText" in n["class"]]
    if not ets:
        print("!! no EditText on editor screen")
        return False
    if dry:
        print(f"[profile] dry-run: would type {text[:50]!r}")
        vpc.sh("input", "keyevent", "KEYCODE_BACK")
        return True
    _tap_xy(ets[0])
    time.sleep(1)
    type_into_focused(text)
    return _save_editor(dry, editor_titles)


def _pick_category(dry):
    """Category opens a picker grid — tap the best-matching cell, then
    Save (no keyboard involved)."""
    if dry:
        print(f"[profile] dry-run: would pick category "
              f"'{BUSINESS['category']}'")
        return True
    nodes = _nodes()
    texts_l = [(n["text"].strip().lower(), n) for n in nodes
               if n["text"].strip()]
    for cand in CATEGORY_CANDIDATES:
        exact = [n for t, n in texts_l if t == cand]
        if exact:
            _tap_xy(exact[0])
            break
    else:
        for cand in CATEGORY_CANDIDATES:
            part = [n for t, n in texts_l if cand in t]
            if part:
                _tap_xy(part[0])
                break
        else:
            print("!! no category cell matched — leaving unset")
            vpc.sh("input", "keyevent", "KEYCODE_BACK")
            return False
    time.sleep(1)
    return tap_first(L_SAVE, timeout=4) is not None


def _goto_edit_profile():
    """Dashboard -> 'Manage profile' -> Edit-profile rows screen."""
    if _on_edit_profile():
        return True
    if not tap_first(L_MANAGE_PROFILE, timeout=6):
        if not tap_first(["profile"], timeout=4):
            return False
    time.sleep(2)
    return _on_edit_profile()


def _complete_profile(dry):
    """Fill the safe Edit-profile fields once the account exists.
    Description (free text) + Category (picker) are automated;
    Business hours + Address need real-world values — reported, not
    guessed. Logo stays a human step."""
    if not _goto_edit_profile():
        print("!! could not reach Edit profile — dump:")
        for t in dump()[:30]:
            print("   •", t[:90])
        return False
    done = []

    if _tap_row_action("description"):
        print("[profile] description editor open")
        if _fill_text_editor(dry, BUSINESS["description"],
                             ["edit description", "description"]):
            done.append("description")
        else:
            # back out to the rows screen for the next field
            vpc.sh("input", "keyevent", "KEYCODE_BACK")
            time.sleep(1.5)
            _goto_edit_profile()
    else:
        print("[profile] description row not found (may be set)")

    if _tap_row_action("category"):
        print("[profile] category picker open")
        if _pick_category(dry):
            done.append("category")
        vpc.sh("input", "keyevent", "KEYCODE_BACK")
        time.sleep(1.5)
        _goto_edit_profile()
    else:
        print("[profile] category row not found (may be set)")

    print(f"[profile] filled: {', '.join(done) or 'nothing'}")
    print("[profile] skipped (need real values / human step): "
          "business hours, address, logo, website verify")
    return True


def cmd_link(_args):
    """Open Business tools -> Chat link and dump the screen — the
    viber.chat link/QR is the public contact surface."""
    if not vpc.pick_device():
        print("no ADB device")
        return 2
    vpc.launch_viber()
    time.sleep(3)
    if not tap_first(L_CONTINUE, timeout=5):
        tap_first(L_BUSINESS_ENTRY, timeout=5)
    time.sleep(2)
    if not tap_first(L_CHAT_LINK, timeout=6):
        print("!! chat link entry not found — dump:")
        for t in dump()[:30]:
            print("   •", t[:90])
        return 3
    time.sleep(2)
    for t in dump()[:30]:
        print("  •", t[:90])
    return 0


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
    joined = " ".join(t for t, _, _ in nodes).lower()
    return ("create your business" in joined
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

    # State machine — three legal starting surfaces:
    #   a) create form (account not yet created)
    #   b) Business tools dashboard / Edit profile (account exists)
    #   c) main app -> navigate via More tab
    if _on_edit_profile() or _on_business_dashboard():
        print("[0] business account already exists — completing profile")
        return 0 if _complete_profile(dry) else 8

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

        # 2. Business entry — either the More-row ("Business Account")
        #    or the "Continue / Finish adding your details" CTA.
        hit = tap_first(L_BUSINESS_ENTRY + L_CONTINUE, timeout=8)
        if not hit:
            print("!! no business-account entry on More — this account "
                  "may already be business, or the entry lives under "
                  "profile. Dump:")
            for t in dump()[:30]:
                print("   •", t[:90])
            return 4
        print(f"[2] tapped: {hit}")

        # 2b. Optional intro screen ("Get Started")
        if tap_first(L_GET_STARTED, timeout=4):
            print("[2b] intro dismissed")
        time.sleep(1.5)

        # 2c. Re-classify — 'Get Started'/Continue can land on the
        #     dashboard (account exists) rather than the create form.
        if _on_edit_profile() or _on_business_dashboard():
            print("[2d] landed on Business tools — completing profile")
            return 0 if _complete_profile(dry) else 8
        if not _on_create_form():
            print("!! unknown post-entry screen — dump:")
            for t in dump()[:30]:
                print("   •", t[:90])
            return 3
        print("[2e] reached 'Create your business'")

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

    # 5. Post-creation profile fields (description/category; hours and
    #    address need real values and are reported, not guessed)
    _complete_profile(dry=False)
    print("done — verify on screen. Remaining human gates: logo image "
          "('Update logo' picker), business-document upload if asked, "
          "directory safety-review acknowledgement.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["probe", "run", "link"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true",
                    help="drive even when the phone's foreground app "
                         "isn't Viber (i.e. someone is using the phone)")
    args = ap.parse_args()
    return {"probe": cmd_probe, "run": cmd_run,
            "link": cmd_link}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())

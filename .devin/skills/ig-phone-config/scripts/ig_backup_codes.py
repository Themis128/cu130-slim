#!/usr/bin/env python3
"""ig_backup_codes.py — generate FRESH Instagram backup codes from the phone's
logged-in IG app (Accounts Center > Password and security > Two-factor
authentication > <account> > Backup codes) and store them canonically.

Why: Meta login 2FA offers WhatsApp/SMS codes that never arrive for this
account, and TOTP seeds are rejected — fresh backup codes are the only
unattended lane left (verified need 2026-11-23).

Usage:
  ig_backup_codes.py                 # full drive: ensure cloudless.gr active,
                                     # navigate, generate, save codes
  ig_backup_codes.py --codes-file=F  # custom output (default ~/ig_backup_codes.txt, 0600)

Codes are written to the file AND stored via cred_set.py META_BACKUP_CODES
(JSON list) — never printed to stdout (only count).

Requires: phone unlocked, not covered, IG installed; reuses
ig_acct_drive.py primitives (same ADB/wireless config).
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SWITCH_DIR = os.path.expanduser(
    "~/cu130-slim/.devin/skills/ig-account-switch-ops/scripts")
sys.path.insert(0, SWITCH_DIR)
import ig_acct_drive as D  # noqa: E402

CRED_SET = os.path.expanduser(
    "~/cu130-slim/.devin/skills/platform-cred-store-ops/scripts/cred_set.py")
OUT = os.path.expanduser("~/ig_backup_codes.txt")

# Ordered tap-path from the profile ☰ menu to the backup-codes screen.
PATH = [
    (r"accounts center|meta account", "Accounts Center entry"),
    (r"password and security", "Password and security"),
    (r"two-factor authentication|two factor", "Two-factor authentication"),
    (r"cloudless\.gr", "cloudless.gr account row"),
    (r"additional methods|more methods|backup codes", "Backup codes area"),
    (r"get new codes|get backup codes|generate|continue", "Generate/continue"),
]


def labeled_nodes(dev):
    xml = D.dump(dev)
    return D.node_boxes(xml) if xml else []


def tap_needle(dev, pattern, timeout=8, desc="", ymax=None):
    """Find labeled node matching regex; tap center; return label or None."""
    rx = re.compile(pattern, re.I)
    deadline = time.time() + timeout
    while time.time() < deadline:
        for label, cx, cy, x1, y1, x2, y2 in labeled_nodes(dev):
            if ymax is not None and cy > ymax:
                continue
            if rx.search(label):
                D.tap_xy(dev, cx, cy)
                print(f"  tap '{desc or label[:40]}' @({cx},{cy})")
                return label
        time.sleep(1.5)
    print(f"  MISS: {desc or pattern}")
    return None


def ensure_ig(dev):
    D.sh(f"adb -s {dev} shell monkey -p com.instagram.android "
         "-c android.intent.category.LAUNCHER 1", dev)
    time.sleep(5)


def active_account(dev):
    out = subprocess.run(
        ["python3", os.path.join(SWITCH_DIR, "ig_acct_drive.py"),
         "account"], capture_output=True, text=True, timeout=30)
    return (out.stdout + out.stderr).strip()


def switch_to_cloudless(dev):
    out = subprocess.run(
        ["python3", os.path.join(SWITCH_DIR, "ig_acct_drive.py"),
         "switch", "cloudless.gr"], capture_output=True, text=True,
        timeout=150)
    print((out.stdout + out.stderr)[-500:])
    s = (out.stdout + out.stderr).lower()
    return "cloudless" in s and "dead" not in s and "timeout" not in s


def open_menu(dev):
    """Profile tab -> Options (top-right hamburger ~y150)."""
    tap_needle(dev, r"^profile$", timeout=5, desc="Profile tab", ymax=2205)
    time.sleep(2)
    hit = tap_needle(dev, r"options|menu", timeout=5, desc="Options",
                     ymax=300)
    if not hit:
        D.tap_xy(dev, 1010, 150)
        print("  tap Options fallback coords")
    time.sleep(3)


def extract_codes(dev):
    text = D.screen_text(dev)
    codes = re.findall(r"\b[A-Z0-9]{8}\b", text)
    seen, out = set(), []
    for c in codes:
        if c not in seen and re.search(r"\d", c) and re.search(r"[A-Z]", c):
            seen.add(c)
            out.append(c)
    return out, text


def store_codes(codes):
    try:
        p = subprocess.run(
            ["python3", CRED_SET, "META_BACKUP_CODES", "--value-stdin"],
            input=json.dumps(codes), text=True, capture_output=True, timeout=60)
        print("cred_set:", "ok" if p.returncode == 0 else p.stderr[-200:])
    except Exception as e:
        print("cred_set failed:", e)


def main():
    global OUT
    for a in sys.argv[1:]:
        if a.startswith("--codes-file="):
            OUT = a.split("=", 1)[1]
    dev = D.pick_device()
    st = D.preflight(dev)
    if st is False:
        print("PREFLIGHT FAIL"); return 2
    ensure_ig(dev)
    acc = active_account(dev)
    print("active account probe:", acc[:120])
    if "cloudless" not in acc.lower():
        print("switching to cloudless.gr…")
        if not switch_to_cloudless(dev):
            print("SWITCH FAILED/SESSION_DEAD — resolve IG session first")
            return 3
        time.sleep(8)
    open_menu(dev)
    for pat, desc in PATH:
        if not tap_needle(dev, pat, timeout=8, desc=desc):
            print("--- screen now:", D.screen_text(dev)[:400])
            return 5
        time.sleep(3)
    time.sleep(2)
    codes, text = extract_codes(dev)
    if len(codes) < 4:
        tap_needle(dev, r"done|show codes|continue|got it", timeout=4,
                   desc="final")
        time.sleep(2)
        codes, text = extract_codes(dev)
    print(f"codes found: {len(codes)}")
    if codes:
        with open(OUT, "w") as f:
            f.write("\n".join(codes) + "\n")
        os.chmod(OUT, 0o600)
        print(f"wrote {OUT} (0600)")
        store_codes(codes)
        return 0
    print("--- screen text tail:", text[-500:])
    return 6


if __name__ == "__main__":
    sys.exit(main())

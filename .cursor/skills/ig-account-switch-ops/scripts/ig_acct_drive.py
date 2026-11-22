#!/usr/bin/env python3
"""ig_acct_drive — switch the active Instagram account on the Galaxy S22+
and convert it to a professional (Business/Creator) account.

Why: the Instagram Graph API (Facebook Login) rejects consumer accounts
with `(#10) Application does not have permission` on /media and /insights.
The only fix is converting the IG profile to Professional in-app — this
drives that flow over wireless ADB.

Subcommands:
  status                 preflight: focus/cover/lock + IG foreground check
  dump [--out F]         print clickable+text nodes on the current screen
  find TEXT              list nodes matching TEXT (text or desc)
  tap-text TEXT          tap first node whose text/desc contains TEXT
  key CODE               keyevent (4=back, 66=enter, ...)
  wait TEXT [--timeout N]  poll dump until TEXT appears
  account                print the username shown on the IG profile tab
  switch USERNAME        best-effort account switch (reports BLOCKED +
                         exact screen if IG exposes no switcher while active)
  convert-pro KIND       KIND = business|creator — run the
                         Account-type-and-tools conversion flow
  verify                 dump the Account type screen and classify
                         personal vs professional

Safety: stops on flip-cover closed, lockscreen, non-IG focus, and on any
checkpoint screen (SMS verify, 2FA, suspicious-login, password). Never
types credentials.
"""
import argparse
import re
import subprocess
import sys
import time

ADB = "adb"
PKG = "com.instagram.android"

# Screens that must never be automated through — hard stop.
CHECKPOINT_MARKERS = [
    "verification code", "verifying your number", "two-factor",
    "two step verification", "suspicious", "confirm your identity",
    "enter your password", "save your login", "was this you",
    "approve", "backup codes",
]


def sh(cmd, device=None):
    full = f"{ADB} -s {device} shell {cmd}" if device else f"{ADB} shell {cmd}"
    return subprocess.run(full, capture_output=True, text=True,
                          timeout=30, shell=True).stdout


def devices():
    out = subprocess.run([ADB, "devices"], capture_output=True, text=True).stdout
    devs = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "device":
            devs.append(parts[0])
    return devs


def pick_device():
    devs = devices()
    if not devs:
        sys.exit("no ADB devices — check wireless debugging / USB lane")
    # prefer tailnet wireless, then LAN, then anything
    for pref in ("100.84.31.85", "192.168.1.77"):
        for d in devs:
            if d.startswith(pref):
                return d
    return devs[0]


def preflight(dev):
    """Return (ok, reason). Mirrors phone-ui-automation rules."""
    focus = sh("dumpsys window | grep mCurrentFocus", dev)
    if "CoverHomeActivity" in focus or "cover" in focus.lower():
        return False, "flip cover closed — ask user to open it"
    if "Keyguard" in focus or "NotificationShade" in focus or "statusbar" in focus.lower():
        return False, "device locked / keyguard showing"
    if PKG not in focus:
        return False, f"Instagram not in focus ({focus.strip()[:80]}) — launch it first"
    return True, "ok"


def dump(dev):
    sh("uiautomator dump /sdcard/_igwin.xml", dev)
    return sh("cat /sdcard/_igwin.xml", dev)


NODE_TAG_RE = re.compile(r'<node\b[^>]*>', re.S)
ATTR_RES = {
    "text": re.compile(r'\btext="([^"]*)"'),
    "desc": re.compile(r'\bcontent-desc="([^"]*)"'),
    "bounds": re.compile(r'\bbounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"'),
}


def node_boxes(xml):
    """Return [(label, cx, cy, x1, y1, x2, y2)] for labeled nodes."""
    out = []
    for m in NODE_TAG_RE.finditer(xml):
        tag = m.group(0)
        t = ATTR_RES["text"].search(tag)
        d = ATTR_RES["desc"].search(tag)
        b = ATTR_RES["bounds"].search(tag)
        label = ((t.group(1) if t else "") or
                 (d.group(1) if d else "")).strip()
        if not label or not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        out.append((label, (x1 + x2) // 2, (y1 + y2) // 2,
                    x1, y1, x2, y2))
    return out


def nodes(xml):
    return [(l, x, y) for l, x, y, *_ in node_boxes(xml)]


def find_box(dev, needle, ymax=None, ymin=None):
    """First node whose label contains needle, optionally y-filtered."""
    for label, x, y, x1, y1, x2, y2 in node_boxes(dump(dev)):
        if needle.lower() not in label.lower():
            continue
        if ymax is not None and y > ymax:
            continue
        if ymin is not None and y < ymin:
            continue
        return label, x, y
    return None


def screen_text(dev):
    return "\n".join(t for t, _, _ in nodes(dump(dev))).lower()


def checkpoint_hit(dev):
    txt = screen_text(dev)
    hits = [m for m in CHECKPOINT_MARKERS if m in txt]
    return hits


def tap_xy(dev, x, y):
    sh(f"input tap {x} {y}", dev)


def tap_text(dev, needle, timeout=6):
    """Poll-dump until a node containing needle appears, then tap it."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for label, x, y in nodes(dump(dev)):
            if needle.lower() in label.lower():
                tap_xy(dev, x, y)
                return label
        time.sleep(1.5)
    return None


def cmd_status(dev, _):
    focus = sh("dumpsys window | grep mCurrentFocus", dev)
    print("focus:", focus.strip()[:140])
    ok, why = preflight(dev)
    print("preflight:", "OK" if ok else f"BLOCKED — {why}")
    hits = checkpoint_hit(dev)
    if hits:
        print("CHECKPOINT markers on screen:", hits)
    return 0 if ok else 2


def cmd_dump(dev, args):
    xml = dump(dev)
    if args.out:
        open(args.out, "w").write(xml)
        print("wrote", args.out)
    for label, x, y in nodes(xml):
        print(f"[{x},{y}] {label[:90]}")
    return 0


def cmd_find(dev, args):
    for label, x, y in nodes(dump(dev)):
        if args.text.lower() in label.lower():
            print(f"[{x},{y}] {label[:90]}")
    return 0


def cmd_key(dev, args):
    sh(f"input keyevent {args.code}", dev)
    return 0


def cmd_wait(dev, args):
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if args.text.lower() in screen_text(dev):
            print("found:", args.text)
            return 0
        time.sleep(1.5)
    print("timeout waiting for:", args.text)
    return 1


def cmd_account(dev, _):
    """Best-effort: username on the IG profile tab."""
    sh("input tap 108 2137", dev)  # profile tab (S22+ nav bar)
    time.sleep(2.5)
    for label, x, y in nodes(dump(dev)):
        if label.startswith("@") or re.fullmatch(r"[a-z0-9._]{3,30}", label):
            print("candidate username:", label)
    return 0


LOGIN_MARKERS = ["password", "log in", "username or email", "mobile number"]


def _wait_text(dev, needles, timeout=8):
    deadline = time.time() + timeout
    while time.time() < deadline:
        txt = screen_text(dev)
        for n in needles:
            if n in txt:
                return n
        time.sleep(1.5)
    return None


def cmd_switch(dev, args):
    """Switch the active IG account — live-verified path (2026-11-23):

      profile tab → tap the header username (top clickable row) →
      account switcher sheet → tap target row → 'Instagram from Meta'
      profile picker → tap target row again → session restore.

    If the target's saved session is dead IG shows a login form — that is
    a human gate (credentials), reported as SESSION_DEAD, never typed.
    """
    ok, why = preflight(dev)
    if not ok:
        sys.exit(f"preflight blocked: {why}")
    want = args.username.lstrip("@").lower()

    # go to profile tab via its nav node
    prof = find_box(dev, "Profile", ymin=2000)
    if prof:
        tap_xy(dev, prof[1], prof[2])
        time.sleep(3)
    else:
        sh("input tap 972 2137", dev)
        time.sleep(3)

    hits = checkpoint_hit(dev)
    if hits:
        sys.exit(f"CHECKPOINT on profile screen: {hits} — manual step needed")

    # header username: a labeled node in the top band that is NOT 'Options'
    # etc. Prefer the current-account username if known, else widest top row.
    header = None
    for label, x, y, x1, y1, x2, y2 in node_boxes(dump(dev)):
        if y > 260 or y1 < 60:
            continue
        if label.strip().startswith("@") or re.fullmatch(
                r"[a-z0-9._]{3,30}", label.strip()):
            header = (label, x, y)
            break
    if not header:
        # fall back: the wide clickable header row
        for label, x, y, x1, y1, x2, y2 in node_boxes(dump(dev)):
            if y < 260 and (x2 - x1) > 500:
                header = (label, x, y)
                break
    if not header:
        cmd_dump(dev, argparse.Namespace(out=None))
        sys.exit("BLOCKED: no profile-header username node — see dump")
    tap_xy(dev, header[1], header[2])
    time.sleep(2.5)

    # switcher sheet should offer 'Add Instagram'/'Dismiss' + account rows
    if not _wait_text(dev, ["add instagram", "dismiss"], timeout=6):
        cmd_dump(dev, argparse.Namespace(out=None))
        sys.exit("BLOCKED: tapping the header did not open the account "
                 "switcher — see dump (IG build may differ)")

    # tap the target account row in the sheet
    row = find_box(dev, want, ymin=900)
    if not row:
        cmd_dump(dev, argparse.Namespace(out=None))
        sys.exit(f"BLOCKED: switcher open but @{want} not listed")
    tap_xy(dev, row[1], row[2])
    time.sleep(3)

    # 'Instagram from Meta' interstitial → tap the target row again
    if _wait_text(dev, ["use another profile", "instagram from meta"],
                  timeout=6):
        row = find_box(dev, want, ymin=900)
        if row:
            tap_xy(dev, row[1], row[2])

    # session restore — poll for outcome
    deadline = time.time() + 25
    while time.time() < deadline:
        txt = screen_text(dev)
        if any(m in txt for m in LOGIN_MARKERS) and "log in" in txt:
            sys.exit("SESSION_DEAD: @{0} needs a fresh login (password/2FA) "
                     "— human gate, credentials are never typed".format(want))
        top = find_box(dev, want, ymax=260)
        if top:
            print(f"switched — @{want} is now the active account")
            return 0
        if "checkpoint" in txt or "verify" in txt or "suspicious" in txt:
            sys.exit(f"CHECKPOINT during switch — markers on screen: {txt[:200]}")
        time.sleep(2)
    sys.exit("TIMEOUT: switch did not settle — run `dump` to see where IG "
             "landed (possible silent revert to previous account)")


def cmd_convert_pro(dev, args):
    """Personal → Professional conversion once the target account is active."""
    kind = args.kind.lower()
    if kind not in ("business", "creator"):
        sys.exit("kind must be business|creator")
    ok, why = preflight(dev)
    if not ok:
        sys.exit(f"preflight blocked: {why}")

    # Profile → Edit profile / Account type and tools.
    sh("input tap 108 2137", dev); time.sleep(2.5)
    if not (tap_text(dev, "Edit profile", timeout=4)
            or tap_text(dev, "Account type", timeout=2)):
        # fall back through settings
        tap_text(dev, "Settings and activity", timeout=4); time.sleep(2.5)
    if not tap_text(dev, "Account type and tools", timeout=5):
        cmd_dump(dev, argparse.Namespace(out=None))
        sys.exit("BLOCKED: 'Account type and tools' not found — see dump")
    time.sleep(2.5)

    hits = checkpoint_hit(dev)
    if hits:
        sys.exit(f"CHECKPOINT during conversion: {hits}")

    # 'Switch to professional account' → choose kind.
    if not tap_text(dev, "Switch to professional", timeout=5):
        cmd_dump(dev, argparse.Namespace(out=None))
        sys.exit("BLOCKED: no 'Switch to professional' entry — account may "
                 "already be professional; run `verify`")
    time.sleep(2.5)
    tap_text(dev, "Business account" if kind == "business" else "Creator",
             timeout=5)
    time.sleep(2.5)

    # Walk remaining pickers (category, contact info, done) generically:
    # tap the primary CTA each screen until we land back on settings/profile.
    for _ in range(8):
        txt = screen_text(dev)
        hits = checkpoint_hit(dev)
        if hits:
            sys.exit(f"CHECKPOINT mid-conversion: {hits}")
        if "account type" in txt and "professional" in txt:
            break
        # prefer forward CTAs in order
        for cand in ("Done", "Next", "Continue", "Save", "Switch",
                     "Not now", "Skip"):
            if tap_text(dev, cand, timeout=1):
                time.sleep(2)
                break
        else:
            break
    print("conversion flow driven — run `verify` and the Graph /media probe")
    return 0


def cmd_verify(dev, _):
    ok, why = preflight(dev)
    if not ok:
        print("BLOCKED:", why)
        return 2
    txt = screen_text(dev)
    if "professional" in txt or "creator" in txt or "business" in txt:
        print("VERDICT: professional indicators present on screen")
    elif "personal" in txt or "switch to professional" in txt:
        print("VERDICT: still personal — 'Switch to professional' offered")
    else:
        print("VERDICT: unclear — run `dump` and read the Account type screen")
    for line in txt.splitlines():
        if any(k in line for k in ("account type", "professional",
                                   "personal", "creator", "business")):
            print("  |", line.strip()[:100])
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["status", "dump", "find", "tap-text",
                                    "key", "wait", "account", "switch",
                                    "convert-pro", "verify"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--timeout", type=float, default=15)
    ap.add_argument("--out")
    a = ap.parse_args()

    dev = pick_device()
    print(f"# device {dev}", file=sys.stderr)

    # map loose args
    ns = argparse.Namespace(out=a.out, timeout=a.timeout)
    if a.cmd == "find":
        ns.text = a.args[0] if a.args else ""
        return cmd_find(dev, ns)
    if a.cmd == "tap-text":
        if not a.args:
            sys.exit("tap-text needs TEXT")
        hit = tap_text(dev, a.args[0])
        print("tapped:", hit or "NOT FOUND")
        return 0 if hit else 1
    if a.cmd == "key":
        ns.code = a.args[0] if a.args else "4"
        return cmd_key(dev, ns)
    if a.cmd == "wait":
        if not a.args:
            sys.exit("wait needs TEXT")
        ns.text = a.args[0]
        return cmd_wait(dev, ns)
    if a.cmd == "switch":
        if not a.args:
            sys.exit("switch needs USERNAME")
        ns.username = a.args[0]
        return cmd_switch(dev, ns)
    if a.cmd == "convert-pro":
        ns.kind = a.args[0] if a.args else "business"
        return cmd_convert_pro(dev, ns)
    return {"status": cmd_status, "dump": cmd_dump, "account": cmd_account,
            "verify": cmd_verify}[a.cmd](dev, ns)


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""termux_drive.py — drive Termux on the Galaxy S22+ from WSL over adb.

Termux accepts injected `input text` (unlike WhatsApp's registration
screens), and termux-api gives us on-device superpowers: SMS inbox reads,
clipboard, notifications. Long/complex commands are pushed as a script file
to /sdcard and executed with `sh` — avoids all `input text` escaping pain.

Subcommands:
  run <cmd>              type+run a short command in the focused termux
  push-run <shell-file>  push a script to /sdcard/tcmd.sh and run it
  screen                 dump the visible terminal text (content-desc)
  sms [-l N]             termux-sms-list -> parse -> print JSON messages
  clipboard <text>       termux-clipboard-set <text>
  watch-sms [seconds]    start a termux-side poll loop -> /sdcard/sms-poll.json
  open                   bring TermuxActivity to front

Env: ADB (adb binary override), ADB_SERIAL (default auto-pick tailnet/USB).
"""

import json
import os
import re
import subprocess
import sys
import time

ADB_CANDIDATES = [
    os.path.expanduser("~/.local/platform-tools/adb"),
    "adb",
    "/mnt/c/Users/tbaltzakis/AppData/Local/Android/Sdk/platform-tools/adb.exe",
]
DEVICE = os.environ.get("ADB_SERIAL") or "100.84.31.85:5555"
FALLBACK_SERIAL = "RFCTA0AEKZP"
_PYW = "/mnt/c/Program Files/Python312/pythonw.exe"
_MCP_HIDE = r"C:\Users\tbaltzakis\devin-scripts\mcp-hide.pyw"


def _adb_bin():
    env = os.environ.get("ADB")
    if env:
        return env
    for c in ADB_CANDIDATES:
        if os.path.exists(c) or (not c.startswith("/") and c):
            try:
                subprocess.run(_a(c, "version"), capture_output=True, timeout=10)
                return c
            except Exception:
                continue
    raise SystemExit("no adb binary found")


def _a(adb, *args):
    """argv — .exe goes via mcp-hide so the Windows spawn doesn't flash."""
    return [_PYW, _MCP_HIDE, adb, *args] if str(adb).endswith(".exe") else [adb, *args]


ADB = _adb_bin()


def sh(*args, timeout=30):
    return subprocess.run(
        _a(ADB, "-s", DEVICE, "shell", *args),
        capture_output=True, text=True, timeout=timeout,
    ).stdout


def pull(remote, local):
    return subprocess.run(
        _a(ADB, "-s", DEVICE, "pull", remote, local),
        capture_output=True, timeout=30,
    )


def open_termux():
    sh("am", "start", "-n", "com.termux/.app.TermuxActivity")
    time.sleep(1.5)


def type_line(cmd):
    """Type a command into the focused termux and press Enter."""
    sh("input", "text", cmd)
    sh("input", "keyevent", "66")


def screen():
    sh("uiautomator", "dump", "/sdcard/ui.xml")
    pull("/sdcard/ui.xml", "/tmp/termux-ui.xml")
    try:
        s = open("/tmp/termux-ui.xml", encoding="utf-8").read()
    except OSError:
        return ""
    m = re.findall(r'content-desc="([^"]{40,})"', s)
    return m[0] if m else ""


def cmd_run(cmd):
    type_line(cmd)


def cmd_push_run(script_path):
    remote = "/sdcard/tcmd.sh"
    subprocess.run(
        _a(ADB, "-s", DEVICE, "push", script_path, remote),
        capture_output=True, timeout=30,
    )
    open_termux()
    type_line(f"sh {remote}")


def cmd_sms(limit):
    out = "/sdcard/termux-sms.json"
    open_termux()
    type_line(f"termux-sms-list -l {limit} > {out}; echo SMSDONE-$?")
    time.sleep(4)
    msgs = None
    for _ in range(4):  # writer can be mid-truncate; retry the pull
        pull(out, "/tmp/termux-sms.json")
        try:
            msgs = json.load(open("/tmp/termux-sms.json"))
            break
        except Exception:
            time.sleep(1.5)
    if msgs is None:
        print("sms parse failed (writer busy or session blocked by a running loop)")
        return
    for m in msgs:
        print(json.dumps(m, ensure_ascii=False))


def cmd_clipboard(text):
    open_termux()
    # clipboard-set takes the text as an argument — keep it printable
    if not re.fullmatch(r"[\x20-\x7e]+", text):
        print("clipboard: only printable ASCII supported via input text")
        return
    type_line(f"termux-clipboard-set {text}")
    time.sleep(1)
    print("clipboard set")


def cmd_watch_sms(seconds):
    loops = max(1, seconds // 4)
    open_termux()
    type_line(
        "for i in $(seq %d); do termux-sms-list -l 15 > "
        "/sdcard/sms-poll.json; sleep 4; done" % loops
    )
    print(f"sms watcher started: {loops} polls -> /sdcard/sms-poll.json")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    op = sys.argv[1]
    if op == "run":
        open_termux()
        cmd_run(" ".join(sys.argv[2:]))
    elif op == "push-run":
        cmd_push_run(sys.argv[2])
    elif op == "screen":
        print(screen())
    elif op == "sms":
        limit = 15
        if "-l" in sys.argv:
            limit = int(sys.argv[sys.argv.index("-l") + 1])
        cmd_sms(limit)
    elif op == "clipboard":
        cmd_clipboard(" ".join(sys.argv[2:]))
    elif op == "watch-sms":
        cmd_watch_sms(int(sys.argv[2]) if len(sys.argv) > 2 else 360)
    elif op == "open":
        open_termux()
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

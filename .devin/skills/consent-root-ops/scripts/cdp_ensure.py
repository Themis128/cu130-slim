#!/usr/bin/env python3
"""cdp_ensure — make Chrome :9222 reachable, trying every launch path.

Order: probe → Windows scheduled task (ChromeDebug) → cmd start →
powershell Start-Process. Reports which path worked.

    cdp_ensure.py          # probe + launch attempts
    cdp_ensure.py probe    # state only
"""
import json
import subprocess
import sys
import time
import urllib.request

CHROME = (r"C:\Program Files\Google\Chrome\Application\chrome.exe")
ARGS = ("--remote-debugging-port=9222 "
        "--user-data-dir=C:\\Users\\tbaltzakis\\chrome-debug-profile")


def probe() -> int:
    try:
        v = json.loads(urllib.request.urlopen(
            "http://localhost:9222/json/version", timeout=5).read())
        print("cdp up:", v.get("Browser", "")[:40])
        return 0
    except Exception:
        print("cdp down")
        return 2


def _win(cmd: str) -> str:
    r = subprocess.run(["cmd.exe", "/c", cmd],
                       capture_output=True, text=True, timeout=30)
    return (r.stdout + r.stderr).strip()


def main() -> int:
    if probe() == 0:
        return 0
    for name, cmd in (
        ("schtasks", 'schtasks /run /tn "ChromeDebug"'),
        ("cmd start", f'start "" /min "{CHROME}" {ARGS} "about:blank"'),
        ("powershell",
         f'powershell.exe -c "Start-Process \'{CHROME}\' '
         f'-ArgumentList \'--remote-debugging-port=9222\','
         f'\'--user-data-dir=C:\\\\Users\\\\tbaltzakis\\\\chrome-debug-profile\','
         f'\'about:blank\'"'),
    ):
        print(f"try {name}:", _win(cmd)[:80])
        time.sleep(8)
        if probe() == 0:
            print(f"via {name}")
            return 0
    print("all paths failed — manual start-chrome-debug.bat needed")
    return 1


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "probe":
        sys.exit(probe())
    sys.exit(main())

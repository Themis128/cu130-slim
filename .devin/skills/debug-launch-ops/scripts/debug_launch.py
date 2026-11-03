#!/usr/bin/env python3
"""debug_launch.py — get :9222 bound where agent launches can't.

    --register   create the ChromeDebug interactive scheduled task + bat
    --run        trigger it (spawns Edge in the desktop session)
    --status     is :9222 listening?
    --write-bat  just drop chrome-debug-launch.bat on C:\\tmp\\

Requires: win_agent on WIN_AGENT (default 100.83.120.93:8765).
"""
import json
import os
import sys
import urllib.request

AGENT = os.environ.get("WIN_AGENT", "http://100.83.120.93:8765")
TASK = "ChromeDebug"
BAT = r"C:\tmp\chrome-debug-launch.bat"
BAT_BODY = r'''@start "" "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222 --remote-allow-origins=* --user-data-dir=C:\tmp\edge-full https://x.com/i/flow/login'''


def agent(cmd: str, timeout: int = 20) -> str:
    req = urllib.request.Request(
        AGENT + "/exec",
        data=json.dumps({"cmd": cmd, "timeout": timeout}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=timeout + 5).read())
        return r.get("out", "")
    except Exception as e:
        return f"agent error: {e}"


def write_bat() -> str:
    b64 = __import__("base64").b64encode(BAT_BODY.encode()).decode()
    return agent(
        f'powershell -Command "[IO.File]::WriteAllBytes(\'{BAT}\',[Convert]::FromBase64String(\'{b64}\'))"'
    )


def status() -> str:
    out = agent("netstat -ano | findstr :9222")
    return "LISTENING" if "LISTENING" in out else "not bound"


def register() -> None:
    print(write_bat() or "bat written")
    # /it → interactive task, only runs when the logon user has a session
    # /sc ONLOGON → rebinds :9222 at every login automatically
    out = agent(
        f'schtasks /create /tn {TASK} /tr "\\"{BAT}\\"" /sc ONLOGON /it /f')
    print("register:", out or "created")


def run() -> None:
    print(agent("taskkill /F /IM msedgewebview2.exe 2>nul"))
    print(agent(f"schtasks /run /tn {TASK}"))


def main() -> int:
    if "--register" in sys.argv:
        register()
        return 0
    if "--run" in sys.argv:
        run()
        return 0
    if "--write-bat" in sys.argv:
        print(write_bat() or "bat written")
        return 0
    if "--status" in sys.argv:
        print(":9222", status())
        return 0
    print("usage: --register | --run | --status | --write-bat")
    return 1


if __name__ == "__main__":
    sys.exit(main())

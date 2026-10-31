#!/usr/bin/env python3
"""chrome_debug_up.py — ensure the real Chrome is up on :9222.

Tries multiple launch paths (cmd.exe, powershell, direct .exe) since
the WSL->Windows boundary drops some invocation forms. Polls the port
and reports. Safe to call repeatedly.
"""
import json
import subprocess
import time
import urllib.request

CHROME = (r"C:\Users\tbaltzakis\AppData\Local\Google\Chrome\Application"
          r"\chrome.exe")
ARGS = ["--remote-debugging-port=9222",
        "--user-data-dir=C:\\Users\\tbaltzakis\\.devin-browser-profile"]


def up():
    try:
        json.loads(urllib.request.urlopen(
            "http://localhost:9222/json/version", timeout=4).read())
        return True
    except Exception:
        return False


def main():
    if up():
        print("already up"); return
    for cmd in (
        ["cmd.exe", "/c", "start", "", CHROME, *ARGS],
        ["powershell.exe", "-Command",
         f"Start-Process '{CHROME}' -ArgumentList '{','.join(ARGS)}'"],
        ["/mnt/c/Users/tbaltzakis/AppData/Local/Google/Chrome/Application/chrome.exe",
         *ARGS],
    ):
        try:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except Exception:
            continue
        for _ in range(15):
            time.sleep(1)
            if up():
                print("chrome :9222 up"); return
    print("FAILED — check Chrome path / profile")


if __name__ == "__main__":
    main()

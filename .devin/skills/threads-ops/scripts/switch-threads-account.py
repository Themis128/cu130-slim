#!/usr/bin/env python3
"""Switch the VNC browser to a saved Instagram/Threads profile.
Usage: switch-threads-account.py [profile_name]  (default: cloudless.gr)"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import bridge, evaluate  # noqa: E402

target = sys.argv[1] if len(sys.argv) > 1 else "cloudless.gr"
print(f"=== Switching to Instagram/Threads profile: {target} ===")

bridge("/session/navigate", {"url": "https://www.instagram.com/accounts/logout/"})
time.sleep(4)
bridge("/session/navigate", {"url": "https://www.instagram.com/accounts/login/"})
time.sleep(6)

expr = (f"(function(){{ var target = {json.dumps(target)}; var btns = Array.from("
        "document.querySelectorAll('button, div[role=button]')).filter(function(b){ "
        "return b.innerText && b.innerText.trim() === target; }); if(btns.length >= 1){ "
        "btns[0].click(); return 'clicked ' + btns.length + ' matches'; } "
        "return 'not found'; }})()")
result = evaluate(expr)
print(f"Profile picker: {result}")
time.sleep(8)

state = evaluate(
    "JSON.stringify({url: document.location.href.substring(0,120), "
    "title: document.title, body: document.body.innerText.substring(0, 200)})")
print("Current page:")
print(state)

print("\nIf the profile was not saved, open noVNC at "
      "http://localhost:6080/vnc.html and log in manually.")

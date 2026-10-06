#!/usr/bin/env python3
"""Shared LinkedIn org field update via browser sidecar debug/eval."""

import json
import sys
import time
import urllib.request
from pathlib import Path

# Callers do `from skill_http import usage` after importing this module.
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))

SIDECAR = "http://localhost:9225"
ORG_ID = "108614163"

SAVE_JS = """
(async () => {
    const btns = document.querySelectorAll('button');
    for (const b of btns) {
        if (b.textContent.trim() === 'Save') { b.click(); await new Promise(r => setTimeout(r, 8000)); return 'saved'; }
    }
    return 'no save button';
})()
"""


def post(path: str, data: dict, timeout: int = 30) -> dict:
    req = urllib.request.Request(
        f"{SIDECAR}{path}", data=json.dumps(data).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception:
        return {}


def evaluate(script: str, timeout: int = 30) -> str:
    return post("/debug/eval", {"script": script}, timeout).get("result", "")


def update_field(selector: str, proto: str, val: str, label: str) -> None:
    print(f"→ Updating LinkedIn org {label} ({len(val)} chars)...")
    post("/debug/navigate", {
        "url": f"https://www.linkedin.com/company/{ORG_ID}/admin/edit/?editPageActiveTab=details"})
    time.sleep(5)

    is_textarea = proto == "HTMLTextAreaElement"
    ret = "'set: ' + t.value.length + ' chars'" if is_textarea else "'set: ' + t.value"
    set_js = f"""
(function(){{
    const t = document.querySelector('{selector}');
    if(!t) return 'not found';
    const setter = Object.getOwnPropertyDescriptor(window.{proto}.prototype, 'value').set;
    setter.call(t, {json.dumps(val)});
    t.dispatchEvent(new Event('input', {{bubbles: true}}));
    t.dispatchEvent(new Event('change', {{bubbles: true}}));
    t.dispatchEvent(new Event('blur', {{bubbles: true}}));
    return {ret};
}})()
"""
    print(f"  Set: {evaluate(set_js, 10)}")
    time.sleep(2)
    print(f"  Save: {evaluate(SAVE_JS, 30)}")

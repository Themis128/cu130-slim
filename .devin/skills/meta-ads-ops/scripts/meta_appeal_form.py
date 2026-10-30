#!/usr/bin/env python3
"""Drive Meta's disabled-ad-account appeal form (help/contact/2026068680760273)
via the facebook-browser-sidecar.

Usage: meta_appeal_form.py
  - navigates to the appeal form, dumps its fields + options, prints the
    exact inputs it needs so a human or a follow-up call can fill them.
    Idempotent — safe to re-run when the sidecar flakes.
"""
import json
import time
import urllib.request

SIDECAR = "http://localhost:9226"
URL = "https://www.facebook.com/help/contact/2026068680760273"


def req(method, path, payload=None, timeout=60):
    r = urllib.request.Request(
        f"{SIDECAR}{path}",
        data=json.dumps(payload).encode() if payload else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    return json.loads(urllib.request.urlopen(r, timeout=timeout).read())


def js(script):
    return req("POST", "/debug/eval", {"script": script}, 30).get("result")


def retry(fn, n=4, wait=10):
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            print(f"  attempt {i+1}: {e}; wait {wait}s")
            time.sleep(wait)
    return None


def main():
    if not retry(lambda: req("POST", "/debug/navigate", {"url": URL})):
        print("navigate failed — sidecar asleep; retry later")
        return
    time.sleep(8)

    t = retry(lambda: req("GET", "/debug/page-text", timeout=20))
    if not t:
        print("page-text failed — retry")
        return
    print("URL:", t.get("url", "")[:130])
    txt = t.get("text", "")
    print(txt[:2000])

    # inventory the form controls
    fields = js(
        "(()=>{const f=[...document.querySelectorAll('input,select,textarea,div[role=combobox],div[role=textbox]')]"
        ".map(e=>({tag:e.tagName||e.getAttribute('role'),name:e.name||e.getAttribute('aria-label')||e.placeholder||'',type:e.type||''}))"
        ".filter(f=>f.name);return JSON.stringify(f.slice(0,15))})()"
    )
    print("\nFIELDS:", fields)

    buttons = js(
        "(()=>{const b=[...document.querySelectorAll('div[role=button],button')]"
        ".map(e=>e.innerText.trim()).filter(t=>t&&t.length<40);return JSON.stringify([...new Set(b)].slice(0,15))})()"
    )
    print("BUTTONS:", buttons)


if __name__ == "__main__":
    main()

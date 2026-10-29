#!/usr/bin/env python3
"""Drive the Meta disabled-ad-account appeal flow via the facebook sidecar.

Usage: meta_appeal.py [step]
  - navigates to Ads Manager, locates the disabled-account banner action
    (Take action / Request review / Contact us), clicks it, and prints the
    resulting dialog text + available buttons so a human or a follow-up
    call can continue. Idempotent — safe to re-run on timeouts.
"""
import json
import sys
import time
import urllib.request

SIDECAR = "http://localhost:9226"
ACT = "657781691826702"


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


def text():
    return req("GET", "/debug/page-text", timeout=20)


def retry(fn, n=3, wait=8):
    for i in range(n):
        try:
            return fn()
        except Exception as e:
            print(f"  attempt {i+1} failed: {e}; retrying in {wait}s")
            time.sleep(wait)
    return None


def main():
    # wake + land on Ads Manager
    if not retry(lambda: req(
        "POST", "/debug/navigate",
        {"url": f"https://adsmanager.facebook.com/adsmanager/manage/accounts?act={ACT}"},
    )):
        print("navigate failed 3x — sidecar asleep; retry later")
        return
    time.sleep(8)

    # find the disabled banner's action button
    buttons = retry(lambda: js(
        "(()=>{const b=[...document.querySelectorAll('div[role=button],button,a')]"
        ".filter(e=>/take action|request review|appeal|contact (support|us)|get help/i"
        ".test(e.innerText||''));return b.map(e=>e.innerText.trim().slice(0,50))"
        ".filter(Boolean).slice(0,10)})()"
    ))
    print("action candidates:", buttons)
    if not buttons:
        print("no appeal button — dump notifications area:")
        print(retry(lambda: js(
            "(()=>{const n=[...document.querySelectorAll('div')]"
            ".filter(e=>e.children.length<8&&/disabled|unusual/i.test(e.innerText||''));"
            "return n.map(e=>e.innerText.slice(0,120)).slice(0,6)})()"
        )))
        return

    # click the best candidate (prefer 'take action' > 'request review' > 'contact')
    clicked = js(
        "(()=>{const pick=[...document.querySelectorAll('div[role=button],button,a')]"
        ".find(e=>/take action|request review/i.test(e.innerText||''))"
        "||[...document.querySelectorAll('div[role=button],button,a')]"
        ".find(e=>/contact|appeal|get help/i.test(e.innerText||''));"
        "if(!pick)return 'none';pick.click();return 'clicked:'+(pick.innerText||'').slice(0,40)})()"
    )
    print("click:", clicked)
    time.sleep(5)

    # dump whatever dialog/page the appeal opened
    dlg = js("(()=>{const d=document.querySelector('[role=dialog]');return d?d.innerText.slice(0,1500):null})()")
    if dlg:
        print("DIALOG:\n", dlg)
    else:
        t = text()
        print("PAGE:", t.get("url", "")[:120])
        print(t.get("text", "")[-1200:])


if __name__ == "__main__":
    main()

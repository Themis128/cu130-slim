#!/usr/bin/env python3
"""fb_gate_wait.py — wait out Meta's device-approval / OTP checkpoint.

Polls the CDP Chrome tab while the "approve this login on another device"
checkpoint is up. If it doesn't clear in --wait seconds, tries the
"Try another way" -> WhatsApp/SMS code path and reads the OTP off the
Galaxy S22+ via ADB (adb devices must be connected).

Usage:
  fb_gate_wait.py                 # poll 10 min for phone-app approval
  fb_gate_wait.py --wait 600 --sms  # then fall back to SMS code + ADB read
"""
import argparse
import json
import re
import subprocess
import sys
import time
import urllib.request

import websocket

CDP = "http://localhost:9222"
ADB = "RFCTA0AEKZP"


def conn():
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json", timeout=8))
    p = [t for t in tabs if t.get("type") == "page"][0]
    ws = websocket.create_connection(p["webSocketDebuggerUrl"],
                                     timeout=15, suppress_origin=True)
    i = [0]
    send(ws, i, "Runtime.enable")
    return ws, i


def send(ws, i, m, p=None):
    i[0] += 1
    ws.send(json.dumps({"id": i[0], "method": m, "params": p or {}}))
    while True:
        r = json.loads(ws.recv())
        if r.get("id") == i[0]:
            return r


def ev(ws, i, x):
    return send(ws, i, "Runtime.evaluate",
                {"expression": x, "returnByValue": True}
                ).get("result", {}).get("result", {}).get("value")


def adb(*a):
    return subprocess.run(["adb", "-s", ADB, *a],
                          capture_output=True, text=True).stdout


def sms_code():
    """Read latest SMS for a 6-8 digit login code via content provider."""
    out = adb("shell", "content", "query", "--uri",
              "content://sms/inbox", "--projection", "body",
              "--sort", "date DESC", "|", "head", "-5")
    codes = re.findall(r"\b(\d{6,8})\b", out)
    return codes[0] if codes else None


def click(ws, i, pat):
    return ev(ws, i, f"""(()=>{{const b=[...document.querySelectorAll(
      'div[role=button],button,a,label')].find(e=>/{pat}/i.test(e.innerText||''));
      if(!b)return 'none';b.click();return 'ok'}})()""")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wait", type=int, default=600)
    ap.add_argument("--sms", action="store_true")
    a = ap.parse_args()
    deadline = time.time() + a.wait
    ws, i = conn()

    while time.time() < deadline:
        st = ev(ws, i, "document.title+'|'+location.href+'||'"
                       "+document.body.innerText.slice(0,400)") or ""
        if "two_factor" not in st and "Waiting for approval" not in st:
            print("APPROVED / past checkpoint:")
            print(st[:600]); return
        print(".", end="", flush=True)
        time.sleep(10)

    print("\nstill waiting — trying Try another way" if a.sms else "")
    if not a.sms:
        return
    if click(ws, i, "try another way") == "none":
        print("no fallback link"); return
    time.sleep(5)
    click(ws, i, "whatsapp|text message|sms")
    time.sleep(2)
    click(ws, i, "continue|next")
    time.sleep(8)
    print("code requested — polling SMS via ADB…")
    for _ in range(12):
        c = sms_code()
        if c:
            print("SMS code:", c)
            r = ev(ws, i, f"""(()=>{{const f=document.querySelector(
              'input[type=text],input[type=tel]');if(!f)return 'none';
              f.focus();f.value='{c}';
              f.dispatchEvent(new Event('input',{{bubbles:true}}));
              const b=[...document.querySelectorAll('button,div[role=button]')]
              .find(e=>/continue|next|confirm|log/i.test(e.innerText||''));
              if(b)b.click();return 'submitted'}})()""")
            print("submit:", r); return
        time.sleep(10)
    print("no SMS code seen")


if __name__ == "__main__":
    main()

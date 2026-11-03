#!/usr/bin/env python3
"""ig_delete.py — delete an Instagram post programmatically.

Graph API cannot delete organic IG media. This drives the working path:
browser-novnc bridge eval login (remembered profile → WhatsApp 2FA code
read off the phone via ADB) → fresh sessionid → instagrapi media_delete.

Run inside social-api (needs httpx + instagrapi). ADB from the WSL host.

    ig_delete.py <media_pk> [--row <post_uuid>]

Post-delete: if the media reports MediaNotFound the post is already gone;
--row then force-removes the stale posts/post_targets rows.
"""
import httpx
import json
import re
import subprocess
import sys
import time

BRIDGE = "http://browser-novnc:9223"
ADB_DEVICE = None  # auto-detect


def _ev(expr: str, timeout: int = 30):
    r = httpx.post(f"{BRIDGE}/session/evaluate",
                   json={"expression": expr},
                   headers={"X-Platform": "instagram"}, timeout=timeout)
    return r.json().get("result")


def _adb(*args: str) -> str:
    dev = ADB_DEVICE
    if dev is None:
        out = subprocess.run(["adb", "devices"], capture_output=True, text=True).stdout
        devs = [l.split()[0] for l in out.splitlines()[1:] if "device" in l and "emulator" not in l]
        if not devs:
            raise RuntimeError("no adb device")
        globals()["ADB_DEVICE"] = dev = devs[0]
    return subprocess.run(["adb", "-s", dev, *args], capture_output=True, text=True).stdout


def _latest_ig_code() -> str | None:
    _adb("shell", "monkey", "-p", "com.whatsapp.w4b",
         "-c", "android.intent.category.LAUNCHER", "1")
    time.sleep(4)
    _adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
    _adb("pull", "/sdcard/ui.xml", "/tmp/wa_ui.xml")
    try:
        x = open("/tmp/wa_ui.xml", encoding="utf-8", errors="ignore").read()
    except FileNotFoundError:
        return None
    codes = re.findall(r"([0-9]{6}) is your Instagram login code", x)
    return codes[-1] if codes else None


def start_ig_session() -> dict:
    for plat in ("threads", "tiktok", "instagram"):
        httpx.post(f"{BRIDGE}/session/stop", headers={"X-Platform": plat}, timeout=15)
        time.sleep(1)
    for _ in range(5):
        r = httpx.post(f"{BRIDGE}/session/start",
                       json={"platform": "instagram"},
                       headers={"X-Platform": "instagram"}, timeout=20)
        if r.status_code == 200:
            return r.json()
        time.sleep(15)
    raise RuntimeError("bridge never freed for instagram")


def login_via_profile() -> None:
    """Click the remembered cloudless.gr profile → WhatsApp 2FA → enter code."""
    time.sleep(12)
    r = _ev("""
      (()=>{const s=[...document.querySelectorAll('span')]
        .find(e=>e.innerText&&e.innerText.trim()==='cloudless.gr');
        if(!s) return 'no-profile'; let el=s;
        for(let i=0;i<8&&el;i++,el=el.parentElement)
          if(el.getAttribute('role')==='button'){el.click();return 'clicked'}
        return 'no-btn'})()
    """)
    if "clicked" not in str(r):
        raise RuntimeError(f"profile click failed: {r}")
    time.sleep(10)
    code = _latest_ig_code()
    if not code:
        raise RuntimeError("no IG code visible in WhatsApp Business")
    r = _ev(f"""
      (()=>{{
        const inp=[...document.querySelectorAll('input')].find(i=>i.type==='text');
        if(inp){{const d=Object.getOwnPropertyDescriptor(inp.__proto__,'value').set;
          d.call(inp,'{code}');inp.dispatchEvent(new Event('input',{{bubbles:true}}));}}
        const c=[...document.querySelectorAll('div[role=button],button')]
          .find(b=>/continue/i.test(b.innerText||b.value||''));
        if(!c) return 'no-continue'; c.click(); return 'ok';
      }})()
    """)
    if "ok" not in str(r):
        raise RuntimeError(f"2FA submit failed: {r}")
    time.sleep(10)
    _ev("""
      (()=>{const b=[...document.querySelectorAll('button,div[role=button],a')]
        .find(e=>/not now|continue|save/i.test(e.innerText||''));
        if(b){b.click();return 'ok'} return 'no-btn'})()
    """)
    time.sleep(8)
    state = _ev("(()=>({url:location.href}))()")
    if "login" in str(state):
        raise RuntimeError(f"still on login flow: {state}")


def fresh_sessionid() -> str:
    r = httpx.get(f"{BRIDGE}/session/cookies",
                  headers={"X-Platform": "instagram"}, timeout=15)
    sid = r.json().get("cookies", {}).get("sessionid", "")
    if not sid:
        raise RuntimeError("no sessionid — not logged in")
    return sid


def media_delete(pk: int, sid: str) -> bool | None:
    """True = deleted, None = already gone, raises on other failure."""
    from instagrapi import Client
    from instagrapi.exceptions import MediaNotFound
    cl = Client()
    cl.delay_range = [1, 3]
    cl.login_by_sessionid(sid)
    try:
        return bool(cl.media_delete(pk))
    except MediaNotFound:
        return None


def drop_local_row(post_id: str) -> None:
    """Remove stale posts + post_targets rows (post already gone on IG)."""
    sql = (
        f"DELETE FROM post_targets WHERE post_id='{post_id}';"
        f"DELETE FROM posts WHERE id='{post_id}';"
    )
    subprocess.run(
        ["docker", "compose", "exec", "-T", "social-postgres",
         "psql", "-U", "social_user", "-d", "social_automation", "-c", sql],
        check=True)


def main() -> int:
    pk = sys.argv[1]
    row = sys.argv[sys.argv.index("--row") + 1] if "--row" in sys.argv else None
    print("starting IG session on bridge…")
    start_ig_session()
    print("logging in via remembered profile + WhatsApp 2FA…")
    login_via_profile()
    sid = fresh_sessionid()
    print("session live; deleting media", pk)
    res = media_delete(int(pk), sid)
    if res is None:
        print("already gone on IG")
        if row:
            drop_local_row(row)
            print(f"local row {row} removed")
    else:
        print("deleted:", res)
    return 0


if __name__ == "__main__":
    sys.exit(main())

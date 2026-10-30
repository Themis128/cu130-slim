#!/usr/bin/env python3
"""skool_ops.py — Skool community ops for the Sofia Kakkava community.

Commands:
    status           — bridge free? session holder? skool login state
    scan [--wait]    — trend_scan with retry-backoff on bridge 409
    push-intravox    — scan → IntraVox en/news/kakkava-digest-<date> page

Run inside social-api (docker compose exec -T social-api python <this>),
or on host — it talks to the bridge over the compose network name only when
inside
on host use BRIDGE_URL=http://127.0.0.1:9223 if the port is published.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

BRIDGE = os.environ.get("BROWSER_BRIDGE_URL", "http://browser-novnc:9223")
COMMUNITY = "https://www.skool.com/sofia-kakkava-coaching"
def _intravox_tool() -> str:
    p = os.environ.get("INTRAVOX_TOOL")
    if p:
        return p
    try:
        return str(__import__("pathlib").Path(__file__).resolve().parents[3]
                   / "intravox-ops/scripts/intravox_tool.py")
    except IndexError:
        return "intravox_tool.py"

LOGIN_PROBE = (
    "JSON.stringify({u:location.href,login:"
    "!document.querySelector('form[action*=login], input[type=password]'),"
    "n:document.body?document.body.innerText.length:0})"
)


def _post(path: str, body: dict, timeout: float = 90.0) -> tuple[int, dict]:
    r = urllib.request.Request(
        BRIDGE + path, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"detail": ""}
    except Exception as e:  # noqa: BLE001
        return 0, {"detail": str(e)}


def _eval(expr: str) -> dict:
    code, data = _post("/session/evaluate", {"expression": expr}, 30.0)
    return {"_status": code, **(data if isinstance(data, dict) else {"r": data})}


def cmd_status() -> int:
    code, data = _post("/session/navigate", {"url": COMMUNITY})
    if code == 409:
        print(f"bridge BUSY — {data.get('detail')}")
        return 1
    if code not in (200, 201):
        print(f"bridge unreachable/failed: {code} {data}")
        return 1
    r = _eval(LOGIN_PROBE)
    try:
        payload = json.loads(r.get("result") or r.get("url") or "{}")
    except Exception:
        payload = {}
    print(f"bridge free — nav {code}")
    print(f"url: {payload.get('u','?')}")
    print(f"logged_in: {payload.get('login')}")
    print(f"body_chars: {payload.get('n','?')}")
    return 0


def _scan_once() -> dict:
    code, data = _post("/session/navigate", {"url": COMMUNITY})
    if code == 409:
        return {"_busy": data.get("detail", "busy")}
    r = _eval(
        "(()=>{const all=[...document.querySelectorAll('div')];"
        "const c=all.filter(e=>{const t=e.innerText||'';"
        "return t.includes('\\u2022')&&t.length>60&&t.length<2500});"
        "const o=c.filter(e=>!c.some(x=>x!==e&&e.contains(x)"
        "&&((x.innerText||'').length>(e.innerText||'').length*0.6)));"
        "return JSON.stringify(o.slice(0,15).map(e=>"
        "({t:(e.innerText||'').slice(0,140),eng:((e.innerText||'').match(/\\d+/g)||['0'])[0]})))})()")
    try:
        return {"top": json.loads(r.get("result") or r.get("url") or "[]")}
    except Exception:
        return {"top": []}


def cmd_scan(wait: bool) -> int:
    tries = 6 if wait else 1
    for i in range(tries):
        out = _scan_once()
        if "_busy" not in out:
            print(json.dumps(out, indent=1, ensure_ascii=False))
            return 0 if out.get("top") else 1
        print(f"bridge busy ({out['_busy']}) — retry {i+1}/{tries} in 30s")
        time.sleep(30)
    print("FAIL: bridge stayed busy")
    return 1


def cmd_login(email: str, password: str) -> int:
    """Store SKOOL_EMAIL/SKOOL_PASSWORD in the platform secret store (D1→PG
    →env), then run the session_healer recovery path to log in now."""
    code = (
        "import asyncio\n"
        "from app.services.secret_store import secret_store\n"
        "from app.services.session_healer import _recover_bridge_platform\n"
        "async def go():\n"
        f"    await secret_store.set('SKOOL_EMAIL', {email!r})\n"
        f"    await secret_store.set('SKOOL_PASSWORD', {password!r})\n"
        "    print('creds stored — healing skool…')\n"
        "    ok = await _recover_bridge_platform('skool')\n"
        "    print('login:', 'OK' if ok else 'FAILED — check status')\n"
        "asyncio.run(go())\n"
    )
    # runs inside social-api where app.* is importable
    r = subprocess.run(
        ["docker", "compose", "exec", "-T", "social-api", "python", "-c", code],
        capture_output=True, text=True, timeout=300)
    print(r.stdout.strip() or r.stderr.strip())
    return 0 if "login: OK" in r.stdout else 1


def cmd_push_intravox() -> int:
    out = _scan_once()
    if "_busy" in out:
        print(f"bridge busy: {out['_busy']}")
        return 1
    top = out.get("top") or []
    if not top:
        print("no posts scraped — login wall or empty feed (run `status` first)")
        return 1
    day = time.strftime("%Y-%m-%d")
    pid = f"kakkava-digest-{day}"
    items = "\\n".join(f"- **eng {p.get('eng','?')}** — {p['t']}" for p in top)
    md = (f"## Skool community digest — {day}\\n\\n"
          f"Source: {COMMUNITY}\\n\\n{items}")
    tool = _intravox_tool()
    r = subprocess.run(
        [sys.executable, tool, "page-create", pid,
         f"Skool digest {day}", "--parent", "en/news"],
        capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    uid = None
    for line in r.stdout.splitlines():
        try:
            uid = json.loads(line[line.find("{"):]).get("uniqueId")
        except Exception:
            pass
    if not uid:
        print("page-create did not yield uniqueId")
        return 1
    w = json.dumps({"type": "text", "content": md})
    r2 = subprocess.run(
        [sys.executable, tool, "widget-add", uid, "--type", "text",
         "--widget", w], capture_output=True, text=True)
    print(r2.stdout.strip() or r2.stderr.strip())
    return 0 if "widgets now" in r2.stdout else 1


def main() -> int:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 1
    if args[0] == "status":
        return cmd_status()
    if args[0] == "scan":
        return cmd_scan("--wait" in args)
    if args[0] == "login":
        em = next((a.split("=", 1)[1] for a in args if a.startswith("--email=")), "")
        pw = next((a.split("=", 1)[1] for a in args if a.startswith("--password=")), "")
        if not em or not pw:
            sys.exit("login needs --email= --password=")
        return cmd_login(em, pw)
    if args[0] == "push-intravox":
        return cmd_push_intravox()
    print(__doc__)
    return 1


if __name__ == "__main__":
    asyncio.set_event_loop(asyncio.new_event_loop()) if False else None
    sys.exit(main())

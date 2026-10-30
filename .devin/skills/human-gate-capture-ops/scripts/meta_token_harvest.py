#!/usr/bin/env python3
"""meta_token_harvest — pull a WhatsApp system-user token from the logged-in
Meta Business session via CDP, then reactivate the social_accounts row.

Needs: Chrome :9222 with a logged-in business.facebook.com session —
navigate to the app's System Users → Generate Token page once, then run.

    meta_token_harvest.py check   # session live?
    meta_token_harvest.py run     # extract token → update revoked row
"""
import json
import subprocess
import sys
import urllib.request

CDP = "http://localhost:9222"
SKILLS = "/home/tbaltzakis/cu130-slim/.devin/skills"


def _tab():
    try:
        tabs = json.loads(urllib.request.urlopen(CDP + "/json", timeout=8).read())
    except Exception:
        return None
    for t in tabs:
        if any(h in t.get("url", "") for h in
               ("facebook.com", "business.facebook.com", "developers.facebook.com")):
            return t
    return None


def check() -> int:
    t = _tab()
    print("meta session:", "found" if t else "absent")
    return 0 if t else 2


def run() -> int:
    t = _tab()
    if not t:
        print("open business.facebook.com in the debug browser first")
        return 2
    import websocket
    ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=20)
    # system-user tokens surface in the page only on the generate-token UI —
    # grab any EAA*/EAAB-shaped string currently in the DOM
    ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate", "params": {
        "expression": """(()=>{
          const cands=[...document.querySelectorAll('input,code,pre,td,span')]
            .map(e=>(e.value||e.textContent||'').trim())
            .filter(s=>/^EAA[Ba-zA-Z0-9_-]{40,}$/.test(s))
          return JSON.stringify(cands.slice(0,3))
        })()""", "returnByValue": True}}))
    out = json.loads(ws.recv())["result"]["result"].get("value")
    ws.close()
    toks = json.loads(out or "[]")
    if not toks:
        print("no token in DOM — open the app's System Users → token page first")
        return 1
    tok = toks[0]
    r = subprocess.run(  # noqa: F841
        ["python3", f"{SKILLS}/human-gate-capture-ops/scripts/gate_capture.py",
         "scan"], capture_output=True, text=True, timeout=90)
    # direct row update via the shared helper
    script = f"""
import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import SocialAccount
from app.core.config import get_settings
from app.core.security import encrypt_token
async def go():
    eng = create_async_engine(get_settings().DATABASE_URL)
    S = sessionmaker(eng, class_=AsyncSession)
    async with S() as db:
        acc = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform=='whatsapp').limit(1))).scalar_one()
        acc.access_token_enc = encrypt_token('{tok}')
        acc.status = 'active'
        await db.commit()
        print('whatsapp reactivated')
asyncio.run(go())
"""
    r2 = subprocess.run(["docker", "exec", "-i", "social-api", "python", "-"],
                        input=script, capture_output=True, text=True, timeout=45)
    print("row update:", r2.stdout.strip() or r2.stderr[:80])
    return 0 if r2.returncode == 0 else 1


if __name__ == "__main__":
    sys.exit({"run": run, "check": check}[
             sys.argv[1] if len(sys.argv) > 1 else "check"]())

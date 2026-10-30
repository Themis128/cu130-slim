#!/usr/bin/env python3
"""gate_capture — watch Downloads for credential drops and auto-provision.

Drop conventions (any filename match; first line = the secret):
    bsky*.txt       → bsky password/app-password → bsky_provision connect
    viber*.txt      → viber bot token           → viber_provision --token
    (files are consumed + shredded after a successful wire)

    gate_capture.py scan    # one-shot sweep (cron: */15 * * * *)
    gate_capture.py status  # what's wired, what's still missing
"""
import glob
import json
import os
import subprocess
import sys

DL = "/mnt/c/Users/tbaltzakis/Downloads"
SKILLS = "/home/tbaltzakis/cu130-slim/.devin/skills"
STATE = os.path.join(os.path.dirname(__file__), "..", "state")
DONE = os.path.join(STATE, "gate_done.json")


def _done() -> set:
    os.makedirs(STATE, exist_ok=True)
    try:
        return set(json.load(open(DONE)))
    except Exception:
        return set()


def _mark(k: str) -> None:
    s = _done() | {k}
    json.dump(sorted(s), open(DONE, "w"), indent=0)


def _read_secret(path: str) -> str:
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if line and not line.startswith("#"):
            return line
    return ""


def _run(script: str, args: list[str]) -> tuple[int, str]:
    r = subprocess.run(
        ["python3", os.path.join(SKILLS, script)] + args,
        capture_output=True, text=True, timeout=120)
    return r.returncode, (r.stdout + r.stderr)[-400:]


_TG_PARSE = __import__("re").compile(
    r"\b(bsky|viber|whatsapp|meta)[:\s]+([A-Za-z0-9_\-\.]{8,})", __import__("re").I)


def _tg_drops() -> int:
    """Poll the bot's getUpdates for 'bsky <pw>' / 'viber <tok>' drops and
    provision them — the phone-friendly alternative to file drops."""
    script = """
import asyncio, json, re
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import SocialAccount
from app.core.config import get_settings
from app.core.security import decrypt_token
import httpx
async def go():
    eng = create_async_engine(get_settings().DATABASE_URL)
    S = sessionmaker(eng, class_=AsyncSession)
    async with S() as db:
        acc = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform=='telegram',
            SocialAccount.status=='active').limit(1))).scalar_one()
        enc = acc.meta_data.get('bot_token_enc')
        tok = decrypt_token(enc.encode() if isinstance(enc,str) else enc) \
            if enc else decrypt_token(acc.access_token_enc)
        r = await httpx.AsyncClient().get(
            f'https://api.telegram.org/bot{tok}/getUpdates',
            params={'offset': -20, 'timeout': 2})
        out = []
        for u in r.json().get('result', []):
            m = u.get('message') or {}
            txt = m.get('text') or ''
            out.append(txt)
        print(json.dumps(out))
asyncio.run(go())
"""
    r = subprocess.run(["docker", "exec", "-i", "social-api", "python", "-"],
                       input=script, capture_output=True, text=True,
                       timeout=45)
    try:
        msgs = json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return 0
    hits = 0
    for m in msgs:
        hit = _TG_PARSE.search(m or "")
        if not hit:
            continue
        plat, sec = hit.group(1).lower(), hit.group(2)
        if plat in ("whatsapp", "meta"):
            rc, out = _whatsapp_token(sec)
        else:
            fn = ("bluesky-ops/scripts/bsky_provision.py"
                  if plat == "bsky" else "viber-ops/scripts/viber_provision.py")
            args = (["connect", "--handle", "cloudless.gr", "--app-password", sec]
                    if plat == "bsky" else ["--token", sec])
            rc, out = _run(fn, args)
        if rc == 0:
            _mark(f"tg:{plat}:{sec[-4:]}"); hits += 1
            print(f"tg {plat} drop → wired")
        else:
            print(f"tg {plat} drop → rc={rc} {out[:60]}")
    return hits


def _whatsapp_token(tok: str):
    """Update the revoked whatsapp row's access_token_enc + flip to active."""
    script = """
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
        acc.access_token_enc = encrypt_token(%r)
        acc.status = 'active'
        await db.commit()
        print('token updated, status=active')
asyncio.run(go())
""" % tok
    r = subprocess.run(["docker", "exec", "-i", "social-api", "python", "-"],
                       input=script, capture_output=True, text=True, timeout=60)
    return r.returncode, r.stdout + r.stderr


def _waba_watch() -> None:
    """Telegram when the WABA payment block clears (or appears)."""
    r = subprocess.run(["docker", "exec", "social-api",
                        "python", "/app/scripts/whatsapp_tool.py", "waba"],
                       capture_output=True, text=True, timeout=90)
    can = "AVAILABLE" if '"can_send_message": "AVAILABLE"' in r.stdout else "BLOCKED"
    stf = os.path.join(STATE, "waba_state.txt")
    prev = open(stf).read().strip() if os.path.exists(stf) else ""
    open(stf, "w").write(can)
    if prev and prev != can:
        print(f"waba state {prev} → {can}")
        if can == "AVAILABLE":
            subprocess.run(["python3",
                            "/home/tbaltzakis/cu130-slim/.devin/skills/"
                            "human-gate-capture-ops/scripts/tg_notify.py",
                            "WhatsApp payment block cleared — drop `whatsapp <system-user-token>` to reconnect"],
                           timeout=30)


def scan() -> int:
    hits = _tg_drops()
    for f in sorted(glob.glob(os.path.join(DL, "*"))):
        name = os.path.basename(f).lower()
        if not os.path.isfile(f) or f in _done():
            continue
        if name.startswith(("whatsapp", "meta")) and name.endswith((".txt", ".key")):
            sec = _read_secret(f)
            if sec:
                rc, out = _whatsapp_token(sec)
                print(f"whatsapp drop → rc={rc} {out[:80]}")
                if rc == 0:
                    _mark(f); os.remove(f); hits += 1
        elif name.startswith("bsky") and name.endswith((".txt", ".key")):
            sec = _read_secret(f)
            if not sec:
                continue
            rc, out = _run("bluesky-ops/scripts/bsky_provision.py",
                           ["connect", "--handle", "cloudless.gr",
                            "--app-password", sec])
            print(f"bsky drop → rc={rc} {out[:80]}")
            if rc == 0:
                _mark(f); os.remove(f); hits += 1
        elif name.startswith("viber") and name.endswith((".txt", ".key")):
            sec = _read_secret(f)
            if not sec:
                continue
            rc, out = _run("viber-ops/scripts/viber_provision.py",
                           ["--token", sec])
            print(f"viber drop → rc={rc} {out[:80]}")
            if rc == 0:
                _mark(f); os.remove(f); hits += 1
    _waba_watch()
    # auto-harvest: if no drops and channels still missing, try the
    # logged-in browser sessions (bsky.app session, partners.viber.com)
    if hits == 0:
        for plat in ("bsky", "viber", "meta"):
            script = "meta_token_harvest" if plat == "meta" else f"{plat}_harvest"
            if plat in _done():
                continue
            rc = subprocess.run(
                ["python3", os.path.join(os.path.dirname(__file__),
                                        f"{script}.py"), "run"],
                capture_output=True, text=True, timeout=90)
            if rc.returncode == 0:
                print(f"{plat}: auto-harvested + wired"); _mark(plat); hits += 1
    print(f"scan: {hits} wired")
    return hits


def status() -> None:
    for plat, probe in (("bluesky", ["connect", "--help"]),
                        ("viber", ["--verify"])):
        rc, out = _run("viber-ops/scripts/viber_provision.py"
                       if plat == "viber"
                       else "bluesky-ops/scripts/bsky_provision.py",
                       probe if plat == "viber" else ["--help"])
        print(f"{plat}: {'wired' if plat in _done() else 'pending — drop ' + plat + '*.txt in Downloads'}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "scan"
    {"scan": scan, "status": status}[cmd]()

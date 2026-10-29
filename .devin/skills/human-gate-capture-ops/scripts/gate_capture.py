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
    r"\b(bsky|viber)[:\s]+([A-Za-z0-9_\-\.]{8,})", __import__("re").I)


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
                       timeout=60)
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
        fn = ("bluesky-ops/scripts/bsky_provision.py"
              if plat == "bsky" else "viber-ops/scripts/viber_provision.py")
        args = (["connect", "--handle", "cloudless.gr", "--password", sec]
                if plat == "bsky" else ["--token", sec])
        rc, out = _run(fn, args)
        if rc == 0:
            _mark(f"tg:{plat}:{sec[-4:]}"); hits += 1
            print(f"tg {plat} drop → wired")
        else:
            print(f"tg {plat} drop → rc={rc} {out[:60]}")
    return hits


def scan() -> int:
    hits = _tg_drops()
    for f in sorted(glob.glob(os.path.join(DL, "*"))):
        name = os.path.basename(f).lower()
        if not os.path.isfile(f) or f in _done():
            continue
        if name.startswith("bsky") and name.endswith((".txt", ".key")):
            sec = _read_secret(f)
            if not sec:
                continue
            rc, out = _run("bluesky-ops/scripts/bsky_provision.py",
                           ["connect", "--handle", "cloudless.gr",
                            "--password", sec])
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
    # auto-harvest: if no drops and channels still missing, try the
    # logged-in browser sessions (bsky.app session, partners.viber.com)
    if hits == 0:
        for plat in ("bsky", "viber"):
            if plat in _done():
                continue
            rc = subprocess.run(
                ["python3", os.path.join(os.path.dirname(__file__),
                                        f"{plat}_harvest.py"), "run"],
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

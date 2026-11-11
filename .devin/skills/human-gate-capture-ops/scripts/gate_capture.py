#!/usr/bin/env python3
"""gate_capture — watch Downloads for credential drops and auto-provision.

Drop conventions (any filename match; first line = the secret):
    bsky*.txt       → bsky password/app-password → bsky_provision connect
    viber*.txt      → viber bot token           → viber_provision --token
    (files are consumed + shredded after a successful wire)

Telegram drops: message the bot "bsky <pw>" / "viber <tok>" /
"whatsapp|meta <token>" — the webhook queues it on Redis ``gate:drops``
and this consumer provisions it (getUpdates can't coexist with the
webhook — HTTP 409).

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


def _docker_exec(args: list[str], stdin: str = "",
                 timeout: int = 60) -> tuple[int, str]:
    """docker exec wrapper that degrades instead of crashing the scan.

    /usr/bin/docker symlinks into /mnt/wsl/docker-desktop — when Docker
    Desktop's WSL distro dies the link dangles and subprocess raises
    FileNotFoundError, which used to abort the WHOLE scan (file drops +
    waba watch skipped) for the duration of the outage.
    """
    try:
        r = subprocess.run(["docker", "exec", "-i"] + args,
                           input=stdin, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
        return 1, f"docker exec unavailable: {e}"


def _redis_drops() -> int:
    """Consume credential drops queued by the Telegram webhook.

    The webhook (app/api/telegram.py) matches ``bsky|viber|whatsapp|meta
    <secret>`` messages and LPUSHes {plat, secret_enc(Fernet), ts} onto the
    Redis list ``gate:drops`` — getUpdates polling is impossible while the
    webhook is registered (HTTP 409). This pops items inside social-api
    (decrypt there, never on the host plaintext-at-rest), provisions them
    with the same dispatch as file drops, and requeues failures (≤3 tries).
    """
    script = """
import asyncio, json
import redis.asyncio as aioredis
from app.core.config import get_settings
from app.core.security import decrypt_token
async def go():
    r = aioredis.from_url(get_settings().REDIS_URL)
    out = []
    for _ in range(25):
        raw = await r.rpop('gate:drops')
        if not raw:
            break
        try:
            d = json.loads(raw)
            d['secret'] = decrypt_token(d['secret_enc'].encode())
            d['_raw'] = raw.decode() if isinstance(raw, bytes) else raw
            out.append(d)
        except Exception:
            pass
    await r.aclose()
    print(json.dumps(out))
asyncio.run(go())
"""
    rc, out = _docker_exec(["social-api", "python", "-"], stdin=script,
                           timeout=45)
    try:
        drops = json.loads(out.strip().splitlines()[-1])
    except Exception:
        return 0
    hits = 0
    for d in drops:
        plat = str(d.get("plat") or "").lower()
        sec = str(d.get("secret") or "")
        if plat not in ("bsky", "viber", "whatsapp", "meta") or len(sec) < 8:
            continue
        if plat in ("whatsapp", "meta"):
            rc, out = _whatsapp_token(sec)
        else:
            fn = ("bluesky-ops/scripts/bsky_provision.py"
                  if plat == "bsky" else "viber-ops/scripts/viber_provision.py")
            args = (["connect", "--handle", "cloudless.gr", "--app-password", sec]
                    if plat == "bsky" else ["--token", sec])
            rc, out = _run(fn, args)
        if rc == 0:
            _mark(f"tg:{plat}:{sec[-4:]}")
            hits += 1
            print(f"tg {plat} drop → wired")
        else:
            print(f"tg {plat} drop → rc={rc} {out[:60]}")
            _requeue_drop(d)
    return hits


def _requeue_drop(d: dict) -> None:
    """Push a failed drop back onto ``gate:drops`` (max 3 retries) so a
    transient provision failure doesn't silently lose the credential."""
    tries = int(d.get("tries") or 0) + 1
    raw = d.get("_raw")
    if tries > 3 or not raw:
        return
    try:
        obj = json.loads(raw)
        obj["tries"] = tries
        payload = json.dumps(obj)
    except Exception:
        return
    script = (
        "import asyncio, redis.asyncio as aioredis\n"
        "from app.core.config import get_settings\n"
        "async def go():\n"
        "    r = aioredis.from_url(get_settings().REDIS_URL)\n"
        f"    await r.lpush('gate:drops', {payload!r})\n"
        "    await r.aclose()\n"
        "asyncio.run(go())\n"
    )
    _docker_exec(["social-api", "python", "-"], stdin=script, timeout=30)


def _whatsapp_token(tok: str):
    """Validate a candidate WA system-user token against Graph API, then
    update the whatsapp row's access_token_enc + flip to active. A bad
    drop must never overwrite the live token — validation runs BEFORE the
    DB write and any Graph error aborts without touching the row."""
    script = """
import asyncio, json, os, urllib.request
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models import SocialAccount
from app.core.config import get_settings
from app.core.security import encrypt_token
TOK = __TOK__
async def go():
    eng = create_async_engine(get_settings().DATABASE_URL)
    S = sessionmaker(eng, class_=AsyncSession)
    async with S() as db:
        acc = (await db.execute(select(SocialAccount).where(
            SocialAccount.platform=='whatsapp').limit(1))).scalar_one()
        md = acc.meta_data or {}
        phone_id = md.get('phone_number_id') or os.environ.get('WHATSAPP_PHONE_NUMBER_ID')
        if not phone_id:
            print('no phone_number_id — cannot validate'); return 2
        url = ('https://graph.facebook.com/v21.0/' + str(phone_id)
               + '?fields=display_phone_number&access_token=' + TOK)
        try:
            d = json.loads(urllib.request.urlopen(url, timeout=15).read())
        except Exception as e:
            print('graph validate failed:', e); return 2
        if 'error' in d or 'display_phone_number' not in d:
            print('token rejected by graph:', json.dumps(d.get('error', d))[:200]); return 2
        acc.access_token_enc = encrypt_token(TOK)
        acc.status = 'active'
        await db.commit()
        print('token validated + updated, status=active')
        return 0
raise SystemExit(asyncio.run(go()))
""".replace("__TOK__", repr(tok))
    return _docker_exec(["social-api", "python", "-"], stdin=script,
                        timeout=60)


def _waba_watch() -> None:
    """Telegram when the WABA payment block clears (or appears)."""
    rc, out = _docker_exec(
        ["social-api", "python", "/app/scripts/whatsapp_tool.py", "waba"],
        timeout=90)
    can = "AVAILABLE" if '"can_send_message": "AVAILABLE"' in out else "BLOCKED"
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
    hits = _redis_drops()
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
                    _mark(f)
                    os.remove(f)
                    hits += 1
        elif name.startswith("bsky") and name.endswith((".txt", ".key")):
            sec = _read_secret(f)
            if not sec:
                continue
            rc, out = _run("bluesky-ops/scripts/bsky_provision.py",
                           ["connect", "--handle", "cloudless.gr",
                            "--app-password", sec])
            print(f"bsky drop → rc={rc} {out[:80]}")
            if rc == 0:
                _mark(f)
                os.remove(f); hits += 1
        elif name.startswith("viber") and name.endswith((".txt", ".key")):
            sec = _read_secret(f)
            if not sec:
                continue
            rc, out = _run("viber-ops/scripts/viber_provision.py",
                           ["--token", sec])
            print(f"viber drop → rc={rc} {out[:80]}")
            if rc == 0:
                _mark(f)
                os.remove(f); hits += 1
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
                print(f"{plat}: auto-harvested + wired")
                _mark(plat); hits += 1
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

#!/usr/bin/env python3
"""x_finalize_watch.py — WSL-side finisher for the X recovery chain.

Polls the Windows daemon status; once OAuth completes:
  1. runs x_finalize.py on Windows (extracts auth_token/ct0 via CDP)
  2. writes X_WEB_AUTH_TOKEN / X_WEB_CT0 / TWITTER_LOGIN_* into .env
  3. restarts social-api (flap-tolerant)
  4. verifies social_accounts.twitter leaves 'expired'

Run: nohup python3 x_finalize_watch.py > x_finalize_watch.log 2>&1 &
"""
import json, os, subprocess, sys, time

WIN = "/mnt/c/Users/tbaltzakis"
DAEMON_STATUSES = [f"{WIN}/x_daemon_status.json", f"{WIN}/x_daemon_status_9445.json"]
FIN_STATUSES = {"9444": f"{WIN}/x_finalize_status.json", "9445": f"{WIN}/x_finalize_status_9445.json"}
COOKIES = {"9444": f"{WIN}/x_cookies.json", "9445": f"{WIN}/x_cookies_9445.json"}
PW_FILE = f"{WIN}/x_new_password.txt"
ENV = "/home/tbaltzakis/cu130-slim/.env"
REPO = "/home/tbaltzakis/cu130-slim"
POLL = 60
DEADLINE = time.time() + 7 * 3600

def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)

def jload(p):
    try:
        return json.load(open(p))
    except Exception:
        return {}

def ps(cmd):
    return subprocess.run(["powershell.exe", "-NoProfile", "-Command", cmd],
                          capture_output=True, text=True, timeout=120)

def env_set(key, val):
    lines = open(ENV).read().splitlines()
    out, done = [], False
    for ln in lines:
        if ln.startswith(key + "="):
            out.append(f"{key}={val}"); done = True
        else:
            out.append(ln)
    if not done:
        out.append(f"{key}={val}")
    open(ENV, "w").write("\n".join(out) + "\n")

def docker_ok(tries=10):
    for _ in range(tries):
        r = subprocess.run(["docker", "ps"], capture_output=True, timeout=15)
        if r.returncode == 0:
            return True
        time.sleep(15)
    return False

def account_status():
    if not docker_ok():
        return "docker-down"
    code = (
        "import asyncio;from sqlalchemy import text;"
        "from app.db.session import async_session_maker;"
        "async def go():\n"
        " async with async_session_maker() as s:\n"
        "  r=await s.execute(text(\"select status from social_accounts where platform='twitter' limit 1\"))\n"
        "  print(r.scalar())\n"
        "asyncio.run(go())"
    )
    r = subprocess.run(["docker", "exec", "social-api", "python3", "-c", code],
                       capture_output=True, text=True, timeout=60, cwd=REPO)
    for ln in r.stdout.splitlines():
        ln = ln.strip()
        if ln in ("active", "expired", "error", "pending", "revoked"):
            return ln
    return "unknown"

def main():
    log("=== x_finalize_watch start ===")
    # phase 1: wait for EITHER daemon to finish (9444 direct, 9445 via WARP)
    port = None
    while time.time() < DEADLINE:
        for p, sp in (("9444", DAEMON_STATUSES[0]), ("9445", DAEMON_STATUSES[1])):
            if jload(sp).get("phase") == "done":
                port = p
                break
        if port:
            break
        time.sleep(POLL)
    if not port:
        log("deadline — no daemon finished"); return
    log(f"daemon on :{port} done — finalizing")
    # phase 2: run cookie extraction on Windows for that port
    ps(f"python C:\\Users\\tbaltzakis\\x_finalize.py {port}")
    fin_status_path = FIN_STATUSES[port]
    for _ in range(30):
        fst = jload(fin_status_path)
        if fst.get("phase") in ("done", "failed", "no_done"):
            break
        time.sleep(10)
    fst = jload(fin_status_path)
    log("finalize status:", fst)
    if fst.get("phase") != "done":
        log("cookie extraction failed — stopping"); return
    ck = jload(COOKIES[port])
    auth_token, ct0 = ck.get("auth_token"), ck.get("ct0")
    if not (auth_token and ct0):
        log("cookies missing auth_token/ct0"); return
    pw = open(PW_FILE).read().strip()
    env_set("X_WEB_AUTH_TOKEN", auth_token)
    env_set("X_WEB_CT0", ct0)
    env_set("TWITTER_LOGIN_USERNAME", "baltzakis.themis@gmail.com")
    env_set("TWITTER_LOGIN_PASSWORD", pw)
    log(".env updated: X_WEB_AUTH_TOKEN, X_WEB_CT0, TWITTER_LOGIN_*")
    # phase 3: recreate services so new env vars actually load
    # (docker restart does NOT re-read .env — compose up -d does)
    if docker_ok():
        subprocess.run(["docker", "compose", "up", "-d", "--no-deps",
                        "social-api", "social-worker-default", "celery-beat"],
                       capture_output=True, text=True, timeout=300, cwd=REPO)
        time.sleep(25)
        for _ in range(10):
            r = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                                "http://localhost:8083/health"], capture_output=True, text=True, timeout=10)
            if r.stdout.strip() == "200":
                break
            time.sleep(15)
        log("social-api health:", r.stdout.strip())
    # phase 4: verify account flipped
    for _ in range(6):
        st = account_status()
        log("twitter account status:", st)
        if st == "active":
            log("=== X RECOVERY COMPLETE ===")
            return
        time.sleep(30)
    log("account still not active — check consent_healers / token probe")

if __name__ == "__main__":
    main()

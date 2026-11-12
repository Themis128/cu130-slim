#!/usr/bin/env python3
"""tg_alert.py — verify + test the severity-routed Telegram alert map.

    tg_alert.py --status    # channel map + bot-admin check per channel
    tg_alert.py --test      # send a test line to each channel
    tg_alert.py <severity> "<text>"   # one-shot alert

Token source order: $TELEGRAM_BOT_TOKEN env -> TELEGRAM_BOT_TOKEN in the
repo-root .env (the canonical file; the live stack also keeps the token
encrypted in Postgres social_accounts). Chat IDs are the -100-prefixed
private-channel IDs from slack_notifications.TG_CHANNELS; TG_CH_* env
vars (also present in .env) override them.
"""
import os
import sys
import urllib.request
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[4]

CH = {
    "critical": os.environ.get("TG_CH_CRITICAL", "-1004389402878"),
    "ops": os.environ.get("TG_CH_OPS", "-1004429103353"),
    "intel": os.environ.get("TG_CH_INTEL", "-1004362141941"),
    "digest": os.environ.get("TG_CH_DIGEST", "-1004363123500"),
}


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    if v:
        return v
    for env_path in (Path(".env"), REPO_ROOT / ".env"):
        try:
            env = env_path.read_text()
        except OSError:
            continue
        for line in env.splitlines():
            if line.startswith(name + "="):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if v:
                    return v
    return None


def _token() -> str | None:
    return _env("TELEGRAM_BOT_TOKEN")


# ntfy fan-out — every severity alert also lands on the phone's ntfy app
# (topic cloudless-alerts; severity maps to ntfy priority 5..2). Best-effort:
# missing config or publish failure never blocks the Telegram send.
_NTFY_PRIO = {"critical": "5", "ops": "4", "intel": "3", "digest": "2"}


def _ntfy_publish(sev: str, text: str) -> str | None:
    base = (_env("NTFY_ALERT_BASE_URL") or "").rstrip("/")
    tok = _env("NTFY_ALERT_TOKEN")
    topic = _env("NTFY_ALERT_TOPIC") or "cloudless-alerts"
    if not (base and tok):
        return None
    body = text.encode()
    r = urllib.request.Request(
        f"{base}/{topic}", data=body, method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Title": f"[{sev}] cloudless",
            "Priority": _NTFY_PRIO.get(sev, "3"),
            "Tags": sev,
            # Cloudflare 403s urllib's default UA — send a browser UA.
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ntfy-alert/1.0",
        })
    try:
        with urllib.request.urlopen(r, timeout=10) as f:
            return "ntfy:" + str(f.status)
    except Exception as e:
        return "ntfy-fail:" + str(e)[:80]


def _api(tok: str, method: str, body: dict) -> dict:
    r = urllib.request.Request(
        f"https://api.telegram.org/bot{tok}/{method}",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(r, timeout=12) as f:
            return json.loads(f.read())
    except Exception as e:
        return {"ok": False, "err": str(e)[:120]}


def send(sev: str, text: str) -> str:
    ntfy_res = _ntfy_publish(sev, text)
    tok = _token()
    if not tok:
        return ntfy_res or "no-bot-token"
    chat = CH.get(sev, CH["ops"])
    r = _api(tok, "sendMessage", {"chat_id": chat, "text": text})
    if r.get("ok"):
        if sev == "critical":
            _api(tok, "sendMessage",
                 {"chat_id": CH["ops"], "text": f"[P1] {text}"})
        return "sent" + (f" ({ntfy_res})" if ntfy_res else "")
    return r.get("err", "fail") + (f" ({ntfy_res})" if ntfy_res else "")


def status() -> str:
    tok = _token()
    if not tok:
        return "no TELEGRAM_BOT_TOKEN in env or .env"
    out = []
    for sev, cid in CH.items():
        r = _api(tok, "getChat", {"chat_id": cid})
        name = r.get("result", {}).get("title", "?") if r.get("ok") else r.get("err")
        out.append(f"{sev:9} {cid} -> {name}")
    return "\n".join(out)


def main() -> int:
    if "--status" in sys.argv:
        print(status())
        return 0
    if "--test" in sys.argv:
        for sev in CH:
            print(sev, send(sev, f"tg-alert test — {sev} channel live"))
        return 0
    if len(sys.argv) >= 3:
        print(send(sys.argv[1], sys.argv[2]))
        return 0
    print("usage: tg_alert.py --status|--test|<severity> '<text>'")
    return 2


if __name__ == "__main__":
    sys.exit(main())

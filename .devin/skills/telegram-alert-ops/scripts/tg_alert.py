#!/usr/bin/env python3
"""tg_alert.py — verify + test the severity-routed Telegram alert map.

    tg_alert.py --status    # channel map + bot-admin check per channel
    tg_alert.py --test      # send a test line to each channel
    tg_alert.py <severity> "<text>"   # one-shot alert
"""
import os
import sys
import urllib.request
import json

CH = {
    "critical": os.environ.get("TG_CH_CRITICAL", "4389402878"),
    "ops": os.environ.get("TG_CH_OPS", "4429103353"),
    "intel": os.environ.get("TG_CH_INTEL", "4362141941"),
    "digest": os.environ.get("TG_CH_DIGEST", "4363123500"),
}


def _token() -> str | None:
    import re
    env = open(".env").read()
    m = re.search(r"TELEGRAM_BOT_TOKEN=(\S+)", env)
    return m.group(1) if m else None


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
    tok = _token()
    if not tok:
        return "no-bot-token"
    chat = CH.get(sev, CH["ops"])
    r = _api(tok, "sendMessage", {"chat_id": chat, "text": text})
    if r.get("ok"):
        if sev == "critical":
            _api(tok, "sendMessage",
                 {"chat_id": CH["ops"], "text": f"[P1] {text}"})
        return "sent"
    return r.get("err", "fail")


def status() -> str:
    tok = _token()
    if not tok:
        return "no TELEGRAM_BOT_TOKEN in .env"
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

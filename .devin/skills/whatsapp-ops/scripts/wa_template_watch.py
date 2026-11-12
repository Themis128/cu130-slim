#!/usr/bin/env python3
"""wa_template_watch.py — alert when a WhatsApp message template changes
review status (PENDING -> APPROVED / REJECTED / PAUSED / DISABLED).

Reads WHATSAPP_ACCESS_TOKEN + WHATSAPP_BUSINESS_ACCOUNT_ID from
~/cu130-slim/.env, GETs /{waba}/message_templates, diffs against
~/.local/state/wa-template-watch/state.json, and TG-alerts #ops on every
transition (rejected_reason included). First run seeds the baseline
silently. Read-only — never creates/edits templates.

Cron: */30 * * * * python3 wa_template_watch.py >> ~/logs/wa-template-watch.log 2>&1
"""
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

ENV = "/home/tbaltzakis/cu130-slim/.env"
LOG = os.path.expanduser("~/logs/wa-template-watch.log")
STATE_DIR = os.path.expanduser("~/.local/state/wa-template-watch")
STATE = os.path.join(STATE_DIR, "state.json")
TG_ALERT = "/home/tbaltzakis/cu130-slim/.devin/skills/telegram-alert-ops/scripts/tg_alert.py"
GRAPH = "https://graph.facebook.com/v21.0"

SEV = {"APPROVED": "✅", "REJECTED": "❌", "PENDING": "⏳",
       "PAUSED": "⏸️", "DISABLED": "🚫", "IN_APPEAL": "📋"}


def log(msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {msg}"
    print(line, flush=True)
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def env_map(path):
    out = {}
    try:
        for line in open(path, errors="replace"):
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
            if m:
                out[m.group(1)] = m.group(2).strip().strip("\"'")
    except OSError:
        pass
    return out


def tg(text, sev="ops"):
    try:
        subprocess.run(["python3", TG_ALERT, sev, text], timeout=30)
    except Exception as e:
        log(f"tg alert failed: {e}")


def fetch_templates(tok, waba):
    url = (f"{GRAPH}/{waba}/message_templates?access_token={tok}"
           "&fields=name,status,category,language,rejected_reason&limit=100")
    with urllib.request.urlopen(url, timeout=20) as r:
        return json.loads(r.read()).get("data", [])


def main():
    cfg = env_map(ENV)
    tok, waba = cfg.get("WHATSAPP_ACCESS_TOKEN"), cfg.get("WHATSAPP_BUSINESS_ACCOUNT_ID")
    if not tok or not waba:
        log("WHATSAPP_ACCESS_TOKEN or WHATSAPP_BUSINESS_ACCOUNT_ID missing — "
            "waiting on wa-coexist-watch / wa-verify to wire them")
        return 0
    try:
        rows = fetch_templates(tok, waba)
    except Exception as e:
        log(f"templates fetch failed: {e}")
        return 1

    cur = {}
    for t in rows:
        key = f"{t.get('name')}|{t.get('language')}"
        cur[key] = {
            "status": t.get("status"),
            "category": t.get("category"),
            "rejected_reason": t.get("rejected_reason"),
        }

    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        prev = json.load(open(STATE))
    except Exception:
        prev = {}

    if not prev:
        json.dump(cur, open(STATE, "w"), indent=1)
        log(f"baseline seeded: {len(cur)} templates")
        return 0

    alerts = []
    for key, meta in cur.items():
        old = prev.get(key)
        if old is None:
            alerts.append(f"🆕 new template `{key}` status={meta['status']}")
        elif old.get("status") != meta["status"]:
            emoji = SEV.get(meta["status"], "•")
            rej = f" — rejected_reason: {meta['rejected_reason']}" \
                if meta.get("rejected_reason") else ""
            alerts.append(
                f"{emoji} template `{key}` {old.get('status')} → {meta['status']}"
                f"{rej}")
    for key in prev:
        if key not in cur:
            alerts.append(f"🗑️ template `{key}` removed from WABA")

    if alerts:
        tg("📋 WhatsApp template status changes:\n" + "\n".join(alerts))
        log("alerted: " + " | ".join(alerts))
    json.dump(cur, open(STATE, "w"), indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

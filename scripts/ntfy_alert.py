#!/usr/bin/env python3
"""ntfy_alert.py — publish an ops alert to the self-hosted ntfy server.

Shared fan-out lane for emitters that already alert via Telegram/Slack but
bypass the tg_alert.py / slack_notifications choke points (skill scripts,
monitor daemons, host watchers). Every call is best-effort: missing config or
publish failure returns a status string, never raises.

Creds: NTFY_ALERT_BASE_URL / NTFY_ALERT_TOKEN / NTFY_ALERT_TOPIC env vars,
falling back to the same keys in the repo-root .env (walked up from this
file, so it resolves on both the WSL and Windows checkouts).

    python3 scripts/ntfy_alert.py "text" [severity]   # one-shot
    from ntfy_alert import send; send("text", "ops")  # library
"""
import os
import sys
import urllib.request
from pathlib import Path

_PRIO = {"critical": "5", "ops": "4", "intel": "3", "digest": "2"}


def _repo_root() -> Path:
    d = Path(__file__).resolve().parent
    for _ in range(8):
        if (d / ".env").exists() and (d / "social-automation").exists():
            return d
        if d.parent == d:
            break
        d = d.parent
    return Path(__file__).resolve().parents[2]


def _env(name: str) -> str:
    v = os.environ.get(name)
    if v:
        return v
    try:
        for line in (_repo_root() / ".env").read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return ""


def send(text: str, severity: str = "ops") -> str:
    """POST text to the ntfy alert topic. Returns 'ntfy:200' or 'ntfy-fail:…'."""
    base = _env("NTFY_ALERT_BASE_URL").rstrip("/")
    tok = _env("NTFY_ALERT_TOKEN")
    topic = _env("NTFY_ALERT_TOPIC") or "cloudless-alerts"
    if not (base and tok):
        return "ntfy:unconfigured"
    req = urllib.request.Request(
        f"{base}/{topic}",
        data=text[:3900].encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {tok}",
            "Title": f"[{severity}] cloudless",
            "Priority": _PRIO.get(severity, "3"),
            "Tags": severity,
            # Cloudflare 403s bare python UAs — send a browser UA.
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) ntfy-alert/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as f:
            return f"ntfy:{f.status}"
    except Exception as e:
        return "ntfy-fail:" + str(e)[:80]


if __name__ == "__main__":
    sev = sys.argv[2] if len(sys.argv) > 2 else "ops"
    print(send(sys.argv[1] if len(sys.argv) > 1 else "ntfy_alert test", sev))

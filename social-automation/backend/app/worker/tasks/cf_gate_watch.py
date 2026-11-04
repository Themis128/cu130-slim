"""CF paid-workers gate watcher — self-running activation chain.

Beat task: every 15 min, run the remaining gate steps in order. Each step
is a no-op when its credential is absent; the chain completes itself the
moment the CF API token lands in env. Escalates to Slack when stuck on a
human gate (agentic-bpm escalation lane).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from celery import shared_task

SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
CHAIN = [
    ("destination", "cf_email_destination.py"),
    ("maildir-confirm", "cf_maildir_scan.py"),
    ("logpush", "cf_logpush_setup.py"),
]
HUMAN_GATE_MESSAGE = (
    "CF gate chain blocked: cf_token_automint could not re-mint "
    "CLOUDFLARE_API_TOKEN via the live dashboard session (Chrome CDP :9222 "
    "must be up with a valid dash login). No manual mint needed — fix the "
    "browser session and the next beat re-runs the whole chain."
)


def _run(script: str, *args: str, timeout: int = 120) -> tuple[bool, str]:
    p = SCRIPTS / script
    if not p.exists():
        return False, f"missing {script}"
    r = subprocess.run(
        [sys.executable, str(p), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode == 0, (r.stdout + r.stderr)[-800:]


def _token_active(token: str) -> bool:
    if not token:
        return False
    import urllib.request

    req = urllib.request.Request("https://api.cloudflare.com/client/v4/user/tokens/verify", headers={"Authorization": f"Bearer {token}"})
    try:
        import json

        return json.load(urllib.request.urlopen(req, timeout=15))["result"]["status"] == "active"
    except Exception:
        return False


@shared_task(name="app.worker.tasks.cf_gate_watch.run")
def run() -> dict:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
    if not _token_active(token):
        ok, out = _run("cf_token_automint.py", timeout=300)
        if ok:
            token = os.environ.get("CLOUDFLARE_API_TOKEN", "").strip()
        else:
            _escalate(HUMAN_GATE_MESSAGE)
            return {"status": "blocked", "gate": "token", "tail": out}

    results: dict[str, dict] = {}
    for name, script in CHAIN:
        ok, out = _run(script)
        results[name] = {"ok": ok, "tail": out}
        if not ok and name != "logpush":  # logpush may legitimately defer on scope
            return {"status": "partial", "failed": name, "results": results}

    if all(r["ok"] for r in results.values()):
        _escalate("CF gate chain complete — destination verified + logpush live")
        return {"status": "complete", "results": results}
    _escalate(f"CF gate chain partial: {results}")
    return {"status": "partial", "results": results}


def _escalate(text: str) -> None:
    try:
        from app.services.slack_notifications import send_telegram_alert
        from app.worker._async import run_async

        run_async(send_telegram_alert("ops", text))
    except Exception:
        import logging

        logging.getLogger(__name__).warning("cf_gate_watch alert: %s", text)

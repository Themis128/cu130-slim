"""Gate-sweep — fires every pending human-gate that has an autonomous bridge
when the debug Chrome (:9222) is live. The consent/OAuth gates stop queuing
for a human click — the cdp_gate_runner drives them on the real session.

Runs hourly: checks pending gates, fires `cdp_gate_runner --once` if any and
the CDP endpoint answers. A gate that needs true human action (a captcha the
bridge can't solve) stays pending — nothing silently fails.
"""
import httpx

from app.worker.celery_app import celery_app

CDP = "http://host.docker.internal:9222/json/version"


def _cdp_live() -> bool:
    try:
        return httpx.get(CDP, timeout=5).status_code == 200
    except Exception:
        return False


def _pending() -> list[str]:
    try:
        import subprocess
        r = subprocess.run(
            ["python", "/app/.devin/skills/gate-cdp-runner/scripts/cdp_gate_runner.py",
             "--list"], capture_output=True, text=True, timeout=15, cwd="/app")
        return [ln.strip() for ln in r.stdout.splitlines() if "pending" in ln.lower()]
    except Exception:
        return []


@celery_app.task(name="app.worker.tasks.gate_sweep.run_gate_sweep")
def run_gate_sweep() -> dict:
    if not _cdp_live():
        return {"skipped": "cdp offline"}
    pend = _pending()
    if not pend:
        return {"fired": 0}
    import subprocess
    r = subprocess.run(
        ["python", "/app/.devin/skills/gate-cdp-runner/scripts/cdp_gate_runner.py",
         "--once"], capture_output=True, text=True, timeout=600, cwd="/app")
    return {"fired": len(pend), "out": (r.stdout + r.stderr)[-400:]}

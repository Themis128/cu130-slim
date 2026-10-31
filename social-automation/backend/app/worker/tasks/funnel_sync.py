"""Funnel-sync beat — EspoCRM→Mautic contact sync + MinIO funnel-zone export.

Runs `scripts/mautic_sync.py --apply` then `scripts/lake_export.py --apply`
as subprocesses (they're entry-point scripts, not importable services).
"""
import subprocess

from app.worker.celery_app import celery_app

_BACKEND = "/app"


def _run(script: str) -> tuple[int, str]:
    r = subprocess.run(
        ["python", f"scripts/{script}", "--apply"], cwd=_BACKEND,
        capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout + r.stderr)[-500:]


@celery_app.task(name="app.worker.tasks.funnel_sync.run_funnel_sync")
def run_funnel_sync() -> dict:
    """Hourly: CRM→Mautic sync then funnel-zone MinIO export."""
    rc1, out1 = _run("mautic_sync.py")
    rc2, out2 = _run("lake_export.py")
    return {"mautic_rc": rc1, "lake_rc": rc2, "mautic": out1, "lake": out2}

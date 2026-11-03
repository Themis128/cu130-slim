"""Funnel-sync beat — EspoCRM→Mautic contact sync + MinIO funnel-zone export.

Runs `scripts/mautic_sync.py --apply` then `scripts/lake_export.py --apply`
as subprocesses (they're entry-point scripts, not importable services).
"""

import subprocess

from app.worker.celery_app import celery_app

_BACKEND = "/app"


def _run(script: str) -> tuple[int, str]:
    r = subprocess.run(["python", f"scripts/{script}", "--apply"], cwd=_BACKEND, capture_output=True, text=True, timeout=600)
    return r.returncode, (r.stdout + r.stderr)[-500:]


def _dbt_run() -> tuple[int, str]:
    """Refresh the funnel dbt models (lead tiers, conversions, engagement)
    into the DuckDB mart — Postgres-attached, writes to /tmp/dbt."""
    import os
    import re

    env = dict(os.environ)
    m = re.search(r"://[^:]+:([^@]+)@", env.get("DATABASE_URL", ""))
    env["PGPASSWORD"] = m.group(1) if m else ""
    env["DBT_LOG_PATH"] = "/tmp/dbt/logs"
    env["DBT_TARGET_PATH"] = "/tmp/dbt/target"
    r = subprocess.run(["dbt", "run", "--profiles-dir", "."], cwd="/app/dbt", capture_output=True, text=True, timeout=600, env=env)
    return r.returncode, (r.stdout + r.stderr)[-500:]


@celery_app.task(name="app.worker.tasks.funnel_sync.run_funnel_sync")
def run_funnel_sync() -> dict:
    """Hourly: CRM→Mautic sync, funnel-zone MinIO export, dbt metrics refresh."""
    rc1, out1 = _run("mautic_sync.py")
    rc2, out2 = _run("lake_export.py")
    rc3, out3 = _dbt_run()
    return {"mautic_rc": rc1, "lake_rc": rc2, "dbt_rc": rc3, "mautic": out1, "lake": out2, "dbt": out3}

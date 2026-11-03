#!/usr/bin/env python3
"""Pipeline funnel gap doctor — the three config gates from the toolkit adoption.

Each check degrades to ``skipped`` when its runtime isn't available:

1. ``team``     — finds which team owns the public-form leads and prints
                  the exact ``CLOUDLESS_WEB_ANALYTICS_TEAM_ID=…`` line to
                  put in .env (the sweep no-ops until this is set).
                  Needs the app env (run inside social-api, or with
                  --env-file .env in a one-off container).
2. ``audit``    — reachability of the ``/api/v1/leads/public`` endpoint the
                  site's /api/audit route forwards to. Creates NO lead:
                  an empty-body POST must return 4xx, not 5xx/timeout.
                  Also reports which URL base is in effect.
3. ``db-env``   — DATABASE_URL (env) vs the live ``social-postgres``
                  container's POSTGRES_* values. Reports MATCH/MISMATCH
                  per field only — secrets are never printed.

    python scripts/pipeline_gap_doctor.py              # all checks
    python scripts/pipeline_gap_doctor.py --check team # one check
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PUBLIC_FORMS = ("newsletter", "free-audit", "audit-request")


def _env_team() -> str:
    return os.environ.get("CLOUDLESS_WEB_ANALYTICS_TEAM_ID", "").strip()


async def check_team() -> dict:
    if _env_team():
        return {"status": "ok", "detail": f"CLOUDLESS_WEB_ANALYTICS_TEAM_ID={_env_team()} already set"}
    try:
        from sqlalchemy import text

        from app.db.session import async_session_maker
    except Exception as exc:  # pragma: no cover - env-dependent
        return {"status": "skipped", "detail": f"app env unavailable ({type(exc).__name__}) — run inside social-api"}
    try:
        async with async_session_maker() as s:
            rows = (
                await s.execute(
                    text("SELECT team_id::text AS tid, count(*) AS n FROM leads WHERE meta_data->>'form' = ANY(:forms) GROUP BY team_id ORDER BY n DESC"),
                    {"forms": list(PUBLIC_FORMS)},
                )
            ).all()
    except Exception as exc:
        return {"status": "fail", "detail": f"lead query failed: {type(exc).__name__} — check DATABASE_URL"}
    if not rows:
        return {"status": "fail", "detail": "no public-form leads found — cannot infer the team id"}
    tid = rows[0].tid
    suffix = f", {len(rows)} teams" if len(rows) > 1 else ""
    detail = f"public leads live on team {tid} ({rows[0].n} rows{suffix})"
    return {
        "status": "action",
        "detail": detail,
        "fix": f"add to .env:  CLOUDLESS_WEB_ANALYTICS_TEAM_ID={tid}\nthen: docker compose restart social-worker-default celery-beat",
    }


async def check_audit(api_base: str) -> dict:
    url = api_base.rstrip("/") + "/api/v1/leads/public"
    try:
        import httpx
    except ImportError:
        return {"status": "skipped", "detail": "httpx missing"}
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            health = await c.get(api_base.rstrip("/") + "/api/v1/health")
            post = await c.post(url, json={})
    except Exception as exc:
        return {"status": "fail", "detail": f"{api_base} unreachable: {type(exc).__name__} — confirm SOCIALAUTO_API_URL in the site's env"}
    ok = health.status_code == 200 and 400 <= post.status_code < 500
    return {
        "status": "ok" if ok else "fail",
        "detail": f"base={api_base} health={health.status_code} empty-post={post.status_code} "
        "(4xx = endpoint live, rejects empty payload; 5xx/timeout = broken)",
        "fix": "" if ok else f"set SOCIALAUTO_API_URL to the reachable API base (tried {api_base})",
    }


def check_db_env() -> dict:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return {"status": "skipped", "detail": "DATABASE_URL not in env — run with the app env"}
    p = urllib.parse.urlparse(url)
    try:
        out = subprocess.run(
            ["docker", "inspect", "social-postgres", "--format", "{{range .Config.Env}}{{.}}\n{{end}}"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if out.returncode != 0:
            return {"status": "skipped", "detail": "social-postgres not inspectable (container down or no docker access)"}
        env = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line)
    except FileNotFoundError:
        return {"status": "skipped", "detail": "no docker CLI — run on the host"}
    checks = {
        "user": p.username == env.get("POSTGRES_USER"),
        "database": (p.path or "").lstrip("/") == env.get("POSTGRES_DB"),
        "password": (p.password or "") == (env.get("POSTGRES_PASSWORD") or ""),
        "host": p.hostname in ("social-postgres", "localhost", "127.0.0.1"),
    }
    bad = [k for k, v in checks.items() if not v]
    if not bad:
        return {"status": "ok", "detail": "DATABASE_URL matches social-postgres (user/db/password/host)"}
    hint = "rotate .env DATABASE_URL to match the container" if "password" in bad or "user" in bad else "check DATABASE_URL"
    return {
        "status": "fail",
        "detail": "mismatch: " + ", ".join(bad) + " (values never printed)",
        "fix": hint + "; if the container was rotated, align .env then restart social-api + workers",
    }


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", choices=("team", "audit", "db-env", "all"), default="all")
    ap.add_argument("--api-base", default=os.environ.get("SOCIALAUTO_API_URL", "https://social.cloudless.gr"))
    args = ap.parse_args()
    checks = {
        "team": lambda: check_team(),
        "audit": lambda: check_audit(args.api_base),
        "db-env": lambda: asyncio.to_thread(check_db_env),
    }
    ran = False
    for name, fn in checks.items():
        if args.check not in ("all", name):
            continue
        ran = True
        r = await fn()
        print(f"[{r['status']:>7}] {name}: {r['detail']}")
        if r.get("fix"):
            print(f"         fix → {r['fix']}")
    if not ran:
        ap.print_help()


if __name__ == "__main__":
    asyncio.run(main())

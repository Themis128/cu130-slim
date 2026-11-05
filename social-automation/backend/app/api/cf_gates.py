"""CF paid-workers gate chain as a platform surface.

Wraps backend/scripts/cf_*.py (token mint → email destination → maildir
confirm → logpush) behind admin-authed endpoints so agents/MCP can drive
the last activation gates without shell access.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth import get_current_user
from app.models.user import User

router = APIRouter()
SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"

TOOLS = {
    "doctor": "cf_workers_doctor.py",
    "destination": "cf_email_destination.py",
    "maildir-confirm": "cf_maildir_scan.py",
    "logpush": "cf_logpush_setup.py",
    "token-mint": "cf_token_mint.py",
}


def _run(script: str, *args: str, timeout: int = 90) -> dict[str, Any]:
    p = SCRIPTS / script
    if not p.exists():
        raise HTTPException(404, f"script not found: {script}")
    try:
        r = subprocess.run(
            [sys.executable, str(p), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"{script} timed out")
    return {"script": script, "exit": r.returncode, "ok": r.returncode == 0, "stdout": r.stdout[-4000:], "stderr": r.stderr[-1500:]}


@router.get("/status")
async def gate_status(_user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Whole gate surface at a glance (doctor --json + token presence)."""
    import os

    out = _run(TOOLS["doctor"], "--json") if (SCRIPTS / TOOLS["doctor"]).exists() else {"ok": False}
    token_set = bool(os.environ.get("CLOUDFLARE_API_TOKEN"))
    return {"token_present": token_set, "doctor": out}


@router.post("/{step}")
async def run_gate_step(step: str, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Run one gate step: destination | maildir-confirm | logpush | doctor | token-mint."""
    script = TOOLS.get(step)
    if not script:
        raise HTTPException(400, f"unknown step {step!r}; one of {sorted(TOOLS)}")
    return _run(script)


@router.post("/token/accept")
async def accept_token(body: dict, _user: User = Depends(get_current_user)) -> dict[str, Any]:
    """Accept a minted CF API token: verify, expose to env, chain destination+confirm+logpush."""
    token = (body.get("token") or "").strip()
    if len(token) < 30:
        raise HTTPException(400, "token too short")
    import os
    import urllib.request

    req = urllib.request.Request(
        "https://api.cloudflare.com/client/v4/user/tokens/verify",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        ok = json.loads(urllib.request.urlopen(req, timeout=20).read()).get("success")
    except Exception as exc:
        raise HTTPException(502, f"verify call failed: {exc}")
    if not ok:
        raise HTTPException(400, "token rejected by /user/tokens/verify")

    env_file = Path("/app/.env")
    if env_file.exists():
        lines = env_file.read_text().splitlines()
        lines = [f"CLOUDFLARE_API_TOKEN={token}" if ln.startswith("CLOUDFLARE_API_TOKEN=") else ln for ln in lines]
        env_file.write_text("\n".join(lines) + "\n")
    os.environ["CLOUDFLARE_API_TOKEN"] = token

    chain = {s: _run(TOOLS[s]) for s in ("destination", "maildir-confirm", "logpush")}
    return {"token": "verified+stored", "chain": chain}

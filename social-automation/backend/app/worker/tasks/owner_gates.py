"""Owner-gate approval pipeline — the reply-loop for human/physical gates.

GitHub issues are the approval surface (per owner-gate-ops): each open issue
carries a data-grounded comment naming the one action that unblocks it. This
beat task closes the loop:

1. Lists open issues on the repo.
2. Reads the latest comment on each — an owner reply matching a keyword:
   - "done"/"fixed"/"uploaded"/"rotated" → the physical act is complete →
     the task closes the issue and posts a Slack confirmation.
   - "approve"/"go"/"yes"/"y" → approval recorded in the state file; the
     gated action stays human-owned (never auto-executed), the digest
     marks it "approved — execute when ready".
3. Posts a Slack digest only when the gate set or reply state CHANGES —
   no daily spam, just a ping the moment there's something new to read.

State lives in a temp JSON file (same pattern as synigoros_status) — a
container restart re-digests once, harmless.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path

import httpx
from celery import shared_task

from app.worker._async import run_async

celery_app = __import__("app.worker.celery_app", fromlist=["celery_app"]).celery_app
celery_app.set_default()
celery_app.set_current()

logger = logging.getLogger(__name__)

REPO = os.environ.get("OWNER_GATES_REPO", "Themis128/cu130-slim")
OWNER = os.environ.get("OWNER_GATES_OWNER", "Themis128")
API = "https://api.github.com"
STATE = Path(tempfile.gettempdir()) / "owner_gates_state.json"

_DONE = re.compile(r"^\W*(done|fixed|uploaded|rotated|completed|submitted)\b", re.I)
_APPROVE = re.compile(r"^\W*(approve|approved|go|yes|ok|y|ship ?it)\b", re.I)


def _token() -> str:
    if t := os.environ.get("GITHUB_TOKEN"):
        return t
    try:
        from app.core.config import get_settings

        return getattr(get_settings(), "GITHUB_TOKEN", "") or ""
    except Exception:
        return ""


async def _gh(token: str, path: str, method: str = "GET", **kw):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(
            method,
            f"{API}{path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"},
            **kw,
        )
    if r.status_code >= 400:
        return None
    return r.json()


async def _open_gates(token: str) -> list[dict]:
    issues = await _gh(token, f"/repos/{REPO}/issues?state=open&per_page=50")
    return [i for i in (issues or []) if not i.get("pull_request")]


async def _latest_owner_comment(token: str, number: int) -> str:
    comments = await _gh(token, f"/repos/{REPO}/issues/{number}/comments?per_page=100")
    for c in reversed(comments or []):
        if (c.get("user") or {}).get("login") == OWNER:
            return (c.get("body") or "").strip()
    return ""


def _load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE.write_text(json.dumps(state))


async def _poll() -> dict:
    from app.services.slack_notifications import post_alert_to_slack

    token = _token()
    if not token:
        return {"skip": "no GITHUB_TOKEN"}
    gates = await _open_gates(token)
    state = _load_state()
    replies = state.get("replies", {})
    actions = []

    for g in gates:
        n = g["number"]
        body = await _latest_owner_comment(token, n)
        if not body:
            continue
        kind = "done" if _DONE.search(body) else "approve" if _APPROVE.search(body) else None
        if kind is None:
            continue
        if replies.get(str(n)) == kind:
            continue  # already consumed
        replies[str(n)] = kind
        if kind == "done":
            await _gh(token, f"/repos/{REPO}/issues/{n}", method="PATCH", json={"state": "closed"})
            actions.append(f":white_check_mark: #{n} closed — owner marked *done*")
        else:
            actions.append(f":ballot_box_with_check: #{n} *approved* — queued gate greenlit")

    open_nums = [g["number"] for g in gates]
    stale = [k for k in replies if int(k) not in open_nums]
    for k in stale:
        replies.pop(k, None)

    digest_needed = bool(actions) or set(open_nums) != set(state.get("open", []))
    state.update({"open": open_nums, "replies": replies})
    _save_state(state)

    if actions:
        await post_alert_to_slack("*Owner-gate replies consumed:*\n" + "\n".join(actions))
    elif digest_needed and open_nums:
        lines = [f"*Owner gates — {len(open_nums)} open*"]
        for g in gates:
            flag = " _(approved)_" if replies.get(str(g["number"])) == "approve" else ""
            lines.append(f"• #{g['number']} {g['title'][:80]}{flag}")
        await post_alert_to_slack("\n".join(lines))
    return {"open": len(open_nums), "actions": len(actions)}


@shared_task(name="app.worker.tasks.owner_gates.poll_owner_gates")
def poll_owner_gates() -> dict:
    """Every 6h — consume owner replies on open issues, digest on change."""
    try:
        return run_async(_poll())
    except Exception as exc:  # noqa: BLE001
        logger.warning("owner-gate poll failed: %s", exc)
        return {"error": str(exc)}

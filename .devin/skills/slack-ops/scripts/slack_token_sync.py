#!/usr/bin/env python3
"""Keep SLACK_BOT_TOKEN in the SocialAuto .env fresh from the mcp-remote Slack session.

Background: all SLACK_WEBHOOK_URL entries in .env are dead (app uninstalled →
404 no_service). The hosted Slack MCP (mcp.slack.com via mcp-remote) holds a
working xoxe user token (chat:write, files:write) in
~/.mcp-auth/mcp-remote-v1/*_tokens.json. slack_notifications.py already falls
back from dead webhooks to SLACK_BOT_TOKEN + channel id, so syncing that token
into .env restores every Slack notification path without an app reinstall.

Tokens last 12h and rotate a refresh_token on each refresh. This script:
  1. picks the Slack-scoped token file (scope contains 'chat:write'),
  2. refreshes it via oauth.v2.user.access when < REFRESH_WINDOW_S remain,
  3. writes rotated tokens back to the mcp-remote file (keeps MCP server in sync),
  4. updates SLACK_BOT_TOKEN in the compose .env,
  5. recreates social-api (--pull never) only when the token actually changed.

Run via cron every 2h:
  0 */2 * * * /usr/bin/python3 /home/tbaltzakis/cu130-slim/.devin/skills/slack-ops/scripts/slack_token_sync.py >> /var/tmp/slack_token_sync.log 2>&1

Env overrides: SLACK_TOKEN_DIR, SOCIALAUTO_ENV, COMPOSE_DIR, SLACK_MCP_CLIENT_ID.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

TOKEN_DIR = Path(
    os.environ.get("SLACK_TOKEN_DIR", str(Path.home() / ".mcp-auth" / "mcp-remote-v1"))
)
ENV_FILE = Path(os.environ.get("SOCIALAUTO_ENV", "/home/tbaltzakis/cu130-slim/.env"))
COMPOSE_DIR = Path(os.environ.get("COMPOSE_DIR", "/home/tbaltzakis/cu130-slim"))
CLIENT_ID = os.environ.get("SLACK_MCP_CLIENT_ID", "1601185624273.8899143856786")
PENDING_MARKER = Path(
    os.environ.get("SLACK_SYNC_PENDING", "/var/tmp/slack_token_sync.pending")
)
CRON_LINE = (
    "0 */2 * * * /usr/bin/python3 /home/tbaltzakis/cu130-slim/.devin/skills/slack-ops/scripts/slack_token_sync.py"
    " >> /var/tmp/slack_token_sync.log 2>&1"
)
# every long-lived service that consumes SLACK_BOT_TOKEN via compose env
# (x-worker-env anchor feeds the four workers; digests run on worker-default;
# n8n reads the token for error-alert chat.postMessage calls — recreating it
# is safe for workflow state, which persists in the n8n DB volume, but NOT
# for in-flight executions: EXECUTIONS_MODE=regular runs them inside the
# n8n process, so a forced recreate mid-run would kill them. _n8n_busy
# defers its recreation while executions are running)
SERVICES = [
    "social-api",
    "social-worker-publishing",
    "social-worker-media",
    "social-worker-default",
    "social-worker-messenger",
    "celery-beat",
    "n8n",
]
TOKEN_ENDPOINT = "https://slack.com/api/oauth.v2.user.access"
REFRESH_WINDOW_S = 2 * 3600  # refresh when <2h remain of the 12h lifetime
# n8n public API — checked before recreating the container so a token
# rollover never aborts a running workflow. Host cron reaches the mapped
# port; the compose-internal http://n8n:5678 would not resolve here.
N8N_API_URL = os.environ.get("N8N_API_URL", "http://localhost:5678")
N8N_BUSY_POLL_S = int(os.environ.get("N8N_BUSY_POLL_S", "30"))
N8N_BUSY_WAIT_S = int(os.environ.get("N8N_BUSY_WAIT_S", "600"))


def _slack_token_files() -> list[tuple[float, Path]]:
    """Return (expires_at_ms, path) for token files holding Slack chat:write scope."""
    out: list[tuple[float, Path]] = []
    for p in TOKEN_DIR.glob("*_tokens.json"):
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if "chat:write" in (d.get("scope") or ""):
            out.append((float(d.get("expires_at") or 0), p))
    return sorted(out, reverse=True)


def _refresh(tok: dict) -> dict:
    body = urllib.parse.urlencode(
        {
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": tok["refresh_token"],
        }
    ).encode()
    req = urllib.request.Request(
        TOKEN_ENDPOINT,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    resp = json.load(urllib.request.urlopen(req, timeout=30))
    if not resp.get("ok"):
        raise RuntimeError(f"oauth.v2.user.access failed: {resp.get('error')}")
    return resp


def _write_env_token(token: str) -> bool:
    """Swap SLACK_BOT_TOKEN in .env. Returns True when the value changed."""
    lines = ENV_FILE.read_text().splitlines()
    changed = False
    out: list[str] = []
    for line in lines:
        if line.startswith("SLACK_BOT_TOKEN="):
            new = f"SLACK_BOT_TOKEN={token}"
            changed = line != new
            out.append(new)
        else:
            out.append(line)
    if not any(ln.startswith("SLACK_BOT_TOKEN=") for ln in lines):
        out.append(f"SLACK_BOT_TOKEN={token}")
        changed = True
    if changed:
        ENV_FILE.write_text("\n".join(out) + "\n")
    return changed


def _env_lookup(name: str) -> str | None:
    """Read one key from the compose .env (no parsing of quotes/comments)."""
    prefix = name + "="
    try:
        for line in ENV_FILE.read_text().splitlines():
            if line.startswith(prefix):
                return line[len(prefix):].strip()
    except OSError:
        pass
    return None


def _n8n_running_executions() -> int | None:
    """Count of currently-running n8n executions, or None on probe failure.

    A probe failure means "safe to recreate": in EXECUTIONS_MODE=regular the
    REST API and the execution engine share the n8n process, so an
    unreachable API implies no live executions to protect (auth failures are
    a config issue a recreate does not make worse).
    """
    key = _env_lookup("N8N_API_KEY")
    if not key:
        return None
    req = urllib.request.Request(
        f"{N8N_API_URL}/api/v1/executions?status=running&limit=1",
        headers={"X-N8N-API-KEY": key},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.load(resp)
    except Exception as exc:  # noqa: BLE001
        print(
            f"n8n executions probe failed ({type(exc).__name__}) — treating as idle",
            file=sys.stderr,
        )
        return None
    return len(data.get("data") or [])


def _n8n_busy() -> bool:
    """Wait briefly for in-flight n8n executions to drain; True when still busy."""
    waited = 0
    while True:
        running = _n8n_running_executions()
        if not running:  # 0 running, or probe failed → safe to recreate
            return False
        if waited >= N8N_BUSY_WAIT_S:
            return True
        print(f"n8n: {running} execution(s) running — waiting {N8N_BUSY_POLL_S}s for drain")
        time.sleep(N8N_BUSY_POLL_S)
        waited += N8N_BUSY_POLL_S


def _recreate_services(targets: list[str]) -> tuple[int, list[str]]:
    """Recreate the given SLACK_BOT_TOKEN consumers.

    Returns (worst compose rc, services still pending). n8n stays pending
    while its executions run — a forced recreate would kill them.
    """
    worst: int = 0
    pending: list[str] = []
    for svc in targets:
        if svc == "n8n" and _n8n_busy():
            print("n8n executions still running — deferring its recreation to the next run")
            pending.append(svc)
            continue
        r = subprocess.run(
            [
                "docker",
                "compose",
                "up",
                "-d",
                "--force-recreate",
                "--no-deps",
                "--pull",
                "never",
                svc,
            ],
            cwd=COMPOSE_DIR,
            check=False,
            timeout=180,
            capture_output=True,
        )
        if r.returncode != 0:
            worst = worst or r.returncode
            pending.append(svc)
            tail = r.stderr.decode(errors="replace").strip()[-200:]
            print(f"compose up {svc} failed rc={r.returncode}: {tail}", file=sys.stderr)
    return worst, pending


def _pending_services() -> list[str]:
    """Services still awaiting recreation from an earlier run.

    Marker holds a JSON list written by this script; a bare marker from
    older runs (or an unreadable file) fails safe to the full consumer set.
    """
    try:
        data = json.loads(PENDING_MARKER.read_text())
        return [s for s in data if s in SERVICES]
    except Exception:
        return list(SERVICES) if PENDING_MARKER.exists() else []


def _install_cron() -> int:
    """Idempotently install the 2h refresh cron (requires crontab)."""
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    existing = cur.stdout if cur.returncode == 0 else ""
    if "slack_token_sync.py" in existing:
        print("cron already installed")
        return 0
    new = existing.rstrip("\n") + "\n" + CRON_LINE + "\n"
    subprocess.run(["crontab", "-"], input=new, check=True, text=True)
    print("cron installed:", CRON_LINE)
    return 0


def main() -> int:
    files = _slack_token_files()
    if not files:
        print("no Slack-scoped token file found", file=sys.stderr)
        return 1
    expires_ms, path = files[0]
    os.chmod(path, 0o600)  # rotated creds must never sit world/group-readable
    tok = json.loads(path.read_text())
    now_ms = time.time() * 1000

    if expires_ms - now_ms < REFRESH_WINDOW_S * 1000:
        resp = _refresh(tok)
        tok["access_token"] = resp["access_token"]
        if resp.get("refresh_token"):
            tok["refresh_token"] = resp["refresh_token"]
        tok["token_type"] = resp.get("token_type", "Bearer")
        tok["expires_in"] = resp.get("expires_in", 43200)
        tok["expires_at"] = int(now_ms + tok["expires_in"] * 1000)
        path.write_text(json.dumps(tok))
        os.chmod(path, 0o600)
        print(f"refreshed token → {path.name}, new expiry in {tok['expires_in']}s")

    changed = _write_env_token(tok["access_token"])
    print(f"env {'updated' if changed else 'unchanged'}")
    targets = list(SERVICES) if changed else _pending_services()
    if targets:
        print(
            f"recreating consumers ({'token change' if changed else 'pending retry'})"
        )
        rc, remaining = _recreate_services(targets)
        done = [s for s in targets if s not in remaining]
        if remaining:
            PENDING_MARKER.write_text(json.dumps(remaining))
            print(
                f"recreated: {', '.join(done) or 'none'}; still pending: "
                f"{', '.join(remaining)} — marker {PENDING_MARKER} set; "
                "next run retries even if the token is unchanged",
                file=sys.stderr,
            )
        else:
            PENDING_MARKER.unlink(missing_ok=True)
            print("recreated:", ", ".join(targets))
        if rc:
            return rc
    return 0


if __name__ == "__main__":
    if "--install-cron" in sys.argv:
        sys.exit(_install_cron())
    sys.exit(main())

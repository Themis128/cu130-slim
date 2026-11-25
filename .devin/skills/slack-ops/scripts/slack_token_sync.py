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
# Additional token dirs to refresh — the WSL devin CLI and the Windows
# mcp-remote each keep INDEPENDENT OAuth grants under their own
# ~/.mcp-auth/mcp-remote-v1 (refresh_token rotation means a copied file
# would kill the other side's grant). The root cron points SLACK_TOKEN_DIR
# at the Windows dir; SLACK_EXTRA_TOKEN_DIRS covers the WSL side.
EXTRA_TOKEN_DIRS = [
    Path(p)
    for p in os.environ.get("SLACK_EXTRA_TOKEN_DIRS", "").split(":")
    if p.strip()
]


def _token_dirs() -> list[Path]:
    dirs = [TOKEN_DIR, *EXTRA_TOKEN_DIRS]
    seen: set[Path] = set()
    return [d for d in dirs if d.is_dir() and not (d in seen or seen.add(d))]


ENV_FILE = Path(os.environ.get("SOCIALAUTO_ENV", "/home/tbaltzakis/cu130-slim/.env"))
# The live stack runs from the standalone compose project — its .env is
# what containers actually read, so every sync writes BOTH files.
EXTRA_ENV_FILES = [
    Path(p) for p in os.environ.get(
        "SOCIALAUTO_EXTRA_ENVS",
        "/home/tbaltzakis/cu130-slim/deploy/socialauto-standalone/.env",
    ).split(",") if p.strip()
]
COMPOSE_DIR = Path(os.environ.get("COMPOSE_DIR", "/home/tbaltzakis/cu130-slim"))
# Recreates target whichever compose project actually owns the service —
# standalone first (live stack), repo-root compose as fallback.
COMPOSE_DIRS = [
    Path(p) for p in os.environ.get(
        "SLACK_SYNC_COMPOSE_DIRS",
        "/home/tbaltzakis/cu130-slim/deploy/socialauto-standalone,/home/tbaltzakis/cu130-slim",
    ).split(",") if p.strip()
]
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
    "social-worker-meta",
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
# graceful-shutdown window for compose stop — n8n refuses new triggers on
# SIGTERM and drains in-flight executions for up to this long before SIGKILL
N8N_STOP_GRACE_S = int(os.environ.get("N8N_STOP_GRACE_S", "120"))
# infisical-sync.py push upserts dotenv keys into Infisical prod. This script
# is the producer of truth for the rotating SLACK_BOT_TOKEN — without the
# mirror below the Infisical copy goes stale every 12h rotation and any
# Infisical-sourced consumer picks up a dead token (seen 2026-11-10).
INFISICAL_SYNC = os.environ.get(
    "INFISICAL_SYNC", "/home/tbaltzakis/home-ops/scripts/infisical-sync.py"
)


def _slack_token_files() -> list[tuple[float, Path]]:
    """Return (expires_at_ms, path) for token files holding Slack chat:write scope."""
    out: list[tuple[float, Path]] = []
    for d in _token_dirs():
        for p in d.glob("*_tokens.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if "chat:write" in (data.get("scope") or ""):
                out.append((float(data.get("expires_at") or 0), p))
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
    """Swap SLACK_BOT_TOKEN in every env file. True when any value changed."""
    changed = False
    for env in [ENV_FILE, *EXTRA_ENV_FILES]:
        if not env.exists():
            continue
        lines = env.read_text().splitlines()
        out: list[str] = []
        dirty = False
        for line in lines:
            if line.startswith("SLACK_BOT_TOKEN="):
                new = f"SLACK_BOT_TOKEN={token}"
                dirty = dirty or line != new
                out.append(new)
            else:
                out.append(line)
        if not any(ln.startswith("SLACK_BOT_TOKEN=") for ln in lines):
            out.append(f"SLACK_BOT_TOKEN={token}")
            dirty = True
        if dirty:
            env.write_text("\n".join(out) + "\n")
            changed = True
    return changed


def _push_token_to_infisical(token: str) -> None:
    """Best-effort: mirror the live SLACK_BOT_TOKEN into Infisical prod.

    Runs every sync (not only on change) so drift self-heals. Never fails
    the sync — the next 2h cron run retries.
    """
    if not Path(INFISICAL_SYNC).exists():
        return
    import tempfile

    tmp = ""
    try:
        with tempfile.NamedTemporaryFile(
            "w", suffix=".env", prefix="slack-token-", delete=False
        ) as f:
            f.write(f"SLACK_BOT_TOKEN={token}\n")
            tmp = f.name
        r = subprocess.run(
            ["python3", INFISICAL_SYNC, "push", tmp, "--overwrite"],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if r.returncode != 0:
            print(
                f"infisical push failed rc={r.returncode}: "
                f"{r.stderr.strip()[-200:]}",
                file=sys.stderr,
            )
        else:
            print("infisical: SLACK_BOT_TOKEN mirrored")
    except Exception as exc:  # noqa: BLE001
        print(f"infisical push error: {exc}", file=sys.stderr)
    finally:
        if tmp:
            Path(tmp).unlink(missing_ok=True)


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


def _n8n_busy_via_db() -> int | None:
    """Count in-flight executions straight from the n8n DB — the source of
    truth the HTTP probe reads anyway. Removes the N8N_API_KEY dependency
    from the token-rotation path: the key in .env had rotted (401), which
    made the probe permanently return "unknown" and defer every n8n
    recreate (seen 2026-11-24)."""
    try:
        r = subprocess.run(
            ["docker", "exec", "-i", "social-postgres", "psql", "-U", "social_user",
             "-d", "n8n", "-tAc",
             "select count(*) from execution_entity "
             "where status in ('running','new','waiting')"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip().isdigit():
            return int(r.stdout.strip())
    except (subprocess.TimeoutExpired, OSError):
        pass
    return None


def _n8n_running_executions() -> int | None:
    """Count of currently-running n8n executions, or None on probe failure.

    None means "unknown", not "idle" — a rejected request (lost scope, bad
    key) or an unreachable API can happen while executions still run.
    """
    n = _n8n_busy_via_db()
    if n is not None:
        return n
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
            f"n8n executions probe failed ({type(exc).__name__}) — state unknown",
            file=sys.stderr,
        )
        return None
    return len(data.get("data") or [])


def _n8n_container_running() -> bool | None:
    """docker inspect truth for the n8n container; None when unreadable."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "n8n", "--format", "{{.State.Running}}"],
            check=False,
            timeout=15,
            capture_output=True,
        )
    except Exception:  # noqa: BLE001
        return None
    if r.returncode != 0:
        return None
    return r.stdout.decode(errors="replace").strip().lower() == "true"


def _n8n_busy() -> bool:
    """Wait for in-flight n8n executions to drain; True when still busy or unknown.

    A probe failure only means "safe to recreate" when docker reports the n8n
    container stopped — no process, nothing to kill. A running (or unreadable)
    container with a failed probe is treated as busy and stays pending.
    """
    poll = max(1, N8N_BUSY_POLL_S)  # POLL_S=0 must not spin forever
    deadline = time.monotonic() + N8N_BUSY_WAIT_S
    while True:
        running = _n8n_running_executions()
        if running is None:
            # API failed or key missing — only a stopped container is safe
            if _n8n_container_running() is False:
                return False
            return True
        if running == 0:
            return False
        if time.monotonic() >= deadline:
            return True
        print(f"n8n: {running} execution(s) running — waiting {poll}s for drain")
        time.sleep(poll)


def _recreate_services(targets: list[str]) -> tuple[int, list[str]]:
    """Recreate the given SLACK_BOT_TOKEN consumers.

    Returns (worst compose rc, services still pending). n8n stays pending
    while its executions run — a forced recreate would kill them.
    """
    worst: int = 0
    pending: list[str] = []
    for svc in targets:
        if svc == "n8n" and _n8n_busy():
            print("n8n executions still running or state unknown — deferring its recreation to the next run")
            pending.append(svc)
            continue
        if svc == "n8n":
            # Drain-check is a snapshot: a schedule/webhook can start between
            # the probe and the recreate. A graceful stop closes the gap —
            # SIGTERM makes n8n refuse new triggers and finish in-flight
            # executions for up to N8N_STOP_GRACE_S before SIGKILL.
            stop = subprocess.run(
                ["docker", "compose", "stop", "-t", str(N8N_STOP_GRACE_S), "n8n"],
                cwd=COMPOSE_DIR,
                check=False,
                timeout=N8N_STOP_GRACE_S + 30,
                capture_output=True,
            )
            if stop.returncode != 0:
                tail = stop.stderr.decode(errors="replace").strip()[-200:]
                print(f"compose stop n8n failed rc={stop.returncode}: {tail}", file=sys.stderr)
                pending.append(svc)
                continue
        r = None
        for cdir in COMPOSE_DIRS:
            if not (cdir / "docker-compose.yml").exists() and not (cdir / "docker-compose.yaml").exists():
                continue
            owns = subprocess.run(
                ["docker", "compose", "ps", "--services", "--filter", "status=running"],
                cwd=cdir, check=False, timeout=30, capture_output=True, text=True,
            )
            if svc not in (owns.stdout or "").split():
                continue
            r = subprocess.run(
                [
                    "docker", "compose", "--env-file", ".env",
                    "up", "-d", "--force-recreate", "--no-deps",
                    "--pull", "never", svc,
                ],
                cwd=cdir,
                check=False,
                timeout=180,
                capture_output=True,
            )
            break
        if r is None:
            print(f"{svc}: not running in any known compose project — skipped", file=sys.stderr)
            pending.append(svc)
            continue
        if r.returncode != 0:
            worst = worst or r.returncode
            pending.append(svc)
            tail = r.stderr.decode(errors="replace").strip()[-200:]
            print(f"compose up {svc} failed rc={r.returncode}: {tail}", file=sys.stderr)
    return worst, pending


def _pending_services() -> list[str]:
    """Services still awaiting recreation from an earlier run.

    Marker holds a JSON list written by this script
    a bare marker from
    older runs (or an unreadable file) fails safe to the full consumer set.
    """
    try:
        data = json.loads(PENDING_MARKER.read_text(encoding="utf-8-sig"))
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


def _slack_api(token: str, method: str, **params) -> dict:
    params["token"] = token
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=urllib.parse.urlencode(params).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.load(resp)
    except Exception:  # noqa: BLE001
        return {}


def _env_token_candidates() -> list[tuple[float, Path, dict]]:
    """Every Slack-scoped token file, refreshed when inside the window."""
    out: list[tuple[float, Path, dict]] = []
    now_ms = time.time() * 1000
    for expires_ms, path in _slack_token_files():
        os.chmod(path, 0o600)  # rotated creds must never sit world/group-readable
        tok = json.loads(path.read_text(encoding="utf-8-sig"))
        if expires_ms - now_ms < REFRESH_WINDOW_S * 1000:
            try:
                resp = _refresh(tok)
            except Exception as exc:  # noqa: BLE001
                print(f"refresh failed for {path.name}: {exc}", file=sys.stderr)
                continue
            tok["access_token"] = resp["access_token"]
            if resp.get("refresh_token"):
                tok["refresh_token"] = resp["refresh_token"]
            tok["token_type"] = resp.get("token_type", "Bearer")
            tok["expires_in"] = resp.get("expires_in", 43200)
            tok["expires_at"] = int(now_ms + tok["expires_in"] * 1000)
            path.write_text(json.dumps(tok))
            os.chmod(path, 0o600)
            print(f"refreshed token → {path.name}, new expiry in {tok['expires_in']}s")
        out.append((float(tok.get("expires_at") or 0), path, tok))
    return sorted(out, key=lambda t: t[0], reverse=True)


def _pick_workspace_token(
    cands: list[tuple[float, Path, dict]],
) -> tuple[Path | None, dict | None]:
    """Prefer the token whose workspace owns SLACK_CHANNEL_ID.

    mcp-remote keeps one token file per connected Slack workspace; with
    several workspaces, picking files[0] is arbitrary and every post lands
    in (or fails against) the wrong workspace. conversations.info resolves
    the channel only inside its own workspace, so the first candidate that
    can see it is the right token. Falls back to the freshest file.
    """
    # Team map first: one token file per workspace, and _team_map.json
    # (written by auth.test probes) already records which team each file
    # belongs to. When SLACK_TEAM_ID names the workspace holding the
    # configured channel IDs, the map answers without any slack.com call —
    # this host cannot always reach slack.com (seen 2026-11-08), and
    # probing every workspace's conversations.info just to find the owner
    # logs a misleading channel_not_found for every non-owning workspace.
    want_team = _env_lookup("SLACK_TEAM_ID")
    if want_team:
        team_map = _team_map()
        for _, path, tok in cands:
            if (team_map.get(path.name) or {}).get("team_id") == want_team:
                return path, tok
    channel = _env_lookup("SLACK_CHANNEL_ID")
    if channel:
        for _, path, tok in cands:
            r = _slack_api(tok["access_token"], "conversations.info", channel=channel)
            if r.get("ok"):
                return path, tok
            print(
                f"{path.name}: cannot see SLACK_CHANNEL_ID "
                f"({r.get('error', 'request failed')})",
                file=sys.stderr,
            )
    if cands:
        return cands[0][1], cands[0][2]
    return None, None


def _team_map() -> dict:
    merged: dict = {}
    for d in _token_dirs():
        try:
            merged.update(
                json.loads((d / "_team_map.json").read_text(encoding="utf-8-sig"))
            )
        except Exception:  # noqa: BLE001
            continue
    return merged


def main() -> int:
    cands = _env_token_candidates()
    if not cands:
        # Nothing to sync is not a failure — the Slack MCP remote simply has
        # no chat:write token file right now. The token-expiry sweep already
        # pages when the env token actually dies; exiting 1 here just spammed
        # ops every cron run.
        print("no Slack-scoped token file found — nothing to sync")
        return 0
    path, tok = _pick_workspace_token(cands)
    if path is None or tok is None:
        return 1

    changed = _write_env_token(tok["access_token"])
    print(f"env {'updated' if changed else 'unchanged'}")
    _push_token_to_infisical(tok["access_token"])
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

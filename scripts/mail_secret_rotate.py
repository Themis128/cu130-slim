#!/usr/bin/env python3
"""mail_secret_rotate.py — rotate MAIL_OUTBOUND_TOKEN end-to-end.

Propagates a fresh Bearer secret through every hop that holds a copy,
in an order that keeps the failure window minimal (send_email falls
back to SMTP on any mail-outbound error, so brief 401s are graceful):

  1. Infisical prod        — source of truth for the daily .env pull
  2. .env files            — WSL checkout + Windows checkout (compose
                             interpolation source; /mnt/c path optional)
  3. CF Worker secret      — PATCH secrets-bulk on mail-outbound
  4. docker compose up -d  — recreate the 6 env consumers (api + 4
                             workers + beat) under .deploy.lock
  5. smoke                 — POST /send must return {"ok":true}

Never prints secret values — fingerprints (sha256[:12]) only.

Env (env var first, then repo-root .env):
  CLOUDFLARE_WORKERS_DEPLOY_TOKEN — Workers Scripts:Edit (secrets-bulk)
  CLOUDFLARE_ACCOUNT_ID           — CF account
  INFISICAL_SITE_URL / INFISICAL_UA_CLIENT_ID / INFISICAL_UA_CLIENT_SECRET
  MAIL_OUTBOUND_URL / MAIL_OUTBOUND_TOKEN (current, for smoke + rollback)
  MAIL_SMOKE_TO                   — smoke recipient (default tbaltzakis@cloudless.gr)

Usage:
  mail_secret_rotate.py --dry-run     # print plan + fingerprints, change nothing
  mail_secret_rotate.py               # rotate for real
  mail_secret_rotate.py --worker NAME --secret KEY   # generic worker/secret
  mail_secret_rotate.py --skip-compose                # no docker step
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPO_ROOT = Path(os.environ.get("MAIL_REPO",
                                Path(__file__).resolve().parents[1]))
INFISICAL_WS = "27660119-de58-4d8b-adaf-a39c8fc8c79e"  # cloudless-infra
INFISICAL_ENV = "prod"
INFISICAL_PATH = "/"
CONSUMERS = ["social-api", "social-worker-publishing", "social-worker-media",
             "social-worker-default", "social-worker-messenger", "celery-beat"]
ENV_FILES = [REPO_ROOT / ".env",
             Path("/mnt/c/Users/tbaltzakis/cu130-slim/.env")]
DEPLOY_LOCK = REPO_ROOT / ".deploy.lock"


def _env(key: str, default: str = "") -> str:
    if val := os.environ.get(key):
        return val.strip()
    try:
        for line in (REPO_ROOT / ".env").read_text().splitlines():
            if line.startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return default


def _fp(v: str) -> str:
    return hashlib.sha256(v.encode()).hexdigest()[:12] if v else "-"


def _http(method: str, url: str, body: dict | None, headers: dict,
          timeout: int = 30) -> tuple[int, dict | str]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        # workers.dev / secrets.cloudless.gr sit behind CF bot protection —
        # the default python-urllib UA gets HTTP 403 error 1010.
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, json.loads(raw)
            except json.JSONDecodeError:
                return r.status, raw[:300].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode("utf-8", "replace")


def step(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _infisical_docker(method: str, path: str, body: dict | None,
                      token: str = "") -> tuple[int, dict | str] | None:
    """Call the Infisical API through the local `infisical` container.

    secrets.cloudless.gr sits behind authentik SSO — the public URL
    302s API calls to the login page, so the only programmatic lane is
    the container-local backend at http://localhost:8080. Returns None
    when docker/container is unavailable (caller falls back to public).
    """
    if subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}",
                       "infisical"], capture_output=True, text=True
                      ).stdout.strip() != "true":
        return None
    env = ["-e", f"TOK={token}", "-e", f"JSON={json.dumps(body) if body is not None else ''}"]
    auth = f'-H "Authorization: Bearer $TOK" ' if token else ""
    inner = (f'curl -s -o /tmp/.inf-resp -w "%{{http_code}}" -X {method} '
             f'"http://localhost:8080{path}" '
             f'{auth}-H "Content-Type: application/json" '
             "-d \"$JSON\"")
    r = subprocess.run(["docker", "exec", *env, "infisical", "sh", "-c", inner],
                       capture_output=True, text=True, timeout=60)
    try:
        code = int(r.stdout.strip() or 0)
    except ValueError:
        return None
    out = subprocess.run(["docker", "exec", "infisical", "cat",
                          "/tmp/.inf-resp"], capture_output=True, text=True,
                         timeout=30).stdout
    try:
        return code, json.loads(out)
    except json.JSONDecodeError:
        return code, out[:300]


def infisical_login_body() -> dict:
    cid, csec = _env("INFISICAL_UA_CLIENT_ID"), _env("INFISICAL_UA_CLIENT_SECRET")
    if not (cid and csec):
        raise RuntimeError("missing INFISICAL_UA_CLIENT_ID/CLIENT_SECRET")
    return {"clientId": cid, "clientSecret": csec}


def infisical_token() -> str:
    # Preferred: container-local login — secrets.cloudless.gr sits behind
    # authentik SSO and 302s public API calls to the login page.
    res = _infisical_docker("POST", "/api/v1/auth/universal-auth/login",
                            infisical_login_body())
    if res and res[0] == 200 and isinstance(res[1], dict) \
            and "accessToken" in res[1]:
        return res[1]["accessToken"]
    site = _env("INFISICAL_SITE_URL").rstrip("/")
    status, resp = _http("POST", f"{site}/api/v1/auth/universal-auth/login",
                         infisical_login_body(), {})
    if status != 200 or not isinstance(resp, dict) or "accessToken" not in resp:
        raise RuntimeError(f"infisical login failed: HTTP {status} {str(resp)[:140]}")
    return resp["accessToken"]


def infisical_upsert(key: str, value: str, token: str) -> None:
    body = {"workspaceId": INFISICAL_WS, "environment": INFISICAL_ENV,
            "secretPath": INFISICAL_PATH, "secretValue": value, "type": "shared"}
    path = f"/api/v3/secrets/raw/{urllib.parse.quote(key, safe='')}"
    res = _infisical_docker("PATCH", path, body, token)
    if res is None or res[0] not in (200, 201):
        # key may not exist yet — create it
        res = _infisical_docker("POST", path, body, token) if res else None
    if res and res[0] in (200, 201):
        return
    if res is None:
        site = _env("INFISICAL_SITE_URL").rstrip("/")
        url = f"{site}{path}"
        status, resp = _http("PATCH", url, body,
                             {"Authorization": f"Bearer {token}"})
        if status not in (200, 201):
            status, resp = _http("POST", url, body,
                                 {"Authorization": f"Bearer {token}"})
        if status not in (200, 201):
            raise RuntimeError(f"infisical upsert {key}: HTTP {status} {str(resp)[:160]}")
        return
    raise RuntimeError(f"infisical upsert {key}: HTTP {res[0]} {str(res[1])[:160]}")


def update_env_files(key: str, value: str) -> list[str]:
    """Replace KEY=... in place, preserving order/comments. Returns touched paths."""
    touched = []
    for path in ENV_FILES:
        if not path.exists():
            continue
        lines = path.read_text().splitlines(keepends=True)
        found = False
        out = []
        for line in lines:
            if line.lstrip().startswith(key + "=") or \
               line.lstrip().startswith("export " + key + "="):
                indent = line[:len(line) - len(line.lstrip())]
                out.append(f"{indent}{key}={value}\n")
                found = True
            else:
                out.append(line)
        if not found:
            out.append(f"{key}={value}\n")
        tmp = path.with_suffix(path.suffix + ".rotate-tmp")
        tmp.write_text("".join(out))
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        touched.append(str(path))
    if not touched:
        raise RuntimeError("no .env file found to update")
    return touched


def cf_secret_bulk(account: str, cf_token: str, worker: str,
                   updates: dict[str, str | None]) -> None:
    secrets_map = {}
    for k, v in updates.items():
        if v is None:
            secrets_map[k] = None
        else:
            secrets_map[k] = {"type": "secret_text", "name": k, "text": v}
    url = (f"https://api.cloudflare.com/client/v4/accounts/{account}"
           f"/workers/scripts/{worker}/secrets-bulk")
    status, resp = _http("PATCH", url, {"secrets": secrets_map},
                         {"Authorization": f"Bearer {cf_token}"})
    ok = isinstance(resp, dict) and resp.get("success")
    if status != 200 or not ok:
        raise RuntimeError(f"CF secrets-bulk: HTTP {status} {str(resp)[:200]}")


def compose_up(services: list[str]) -> None:
    if DEPLOY_LOCK.exists():
        try:
            age = time.time() - DEPLOY_LOCK.stat().st_mtime
            if age < 3600:
                raise RuntimeError(f"fresh .deploy.lock present ({int(age)}s) — "
                                   f"another deploy in progress; aborting")
        except OSError:
            pass
    DEPLOY_LOCK.write_text(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} "
                           f"mail_secret_rotate compose up -d {' '.join(services)}\n")
    try:
        r = subprocess.run(
            ["docker", "compose", "up", "-d", *services],
            cwd=REPO_ROOT, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(f"compose up failed: {(r.stderr or r.stdout)[-300:]}")
        step("compose up -d done: " + ", ".join(services))
    finally:
        try:
            DEPLOY_LOCK.unlink()
        except OSError:
            pass


def smoke_send(url: str, token: str, to: str, worker: str) -> None:
    body = {"to": to, "subject": f"mail-secret-rotate smoke {int(time.time())}",
            "text": f"MAIL_OUTBOUND_TOKEN rotation smoke for {worker} — "
                    f"ignore/delete.", "category": "ops-alert"}
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 method="POST",
                                 headers={"Authorization": f"Bearer {token}",
                                          "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"smoke HTTP {e.code}: {e.read()[:160]}")
    if not resp.get("ok"):
        raise RuntimeError(f"smoke rejected: {str(resp)[:160]}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--worker", default="mail-outbound")
    ap.add_argument("--secret", default="MAIL_OUTBOUND_TOKEN")
    ap.add_argument("--skip-compose", action="store_true")
    ap.add_argument("--env-file", action="append", default=None,
                    help="extra .env paths to update (repeatable)")
    args = ap.parse_args()

    old = _env(args.secret)
    new = secrets.token_hex(32)
    account = _env("CLOUDFLARE_ACCOUNT_ID")
    cf_tok = _env("CLOUDFLARE_WORKERS_DEPLOY_TOKEN") or _env("CLOUDFLARE_API_TOKEN")
    out_url = _env("MAIL_OUTBOUND_URL")
    smoke_to = _env("MAIL_SMOKE_TO", "tbaltzakis@cloudless.gr")

    print(f"worker={args.worker} secret={args.secret}")
    print(f"old token sha={_fp(old)}  new token sha={_fp(new)}")
    for p in ENV_FILES + [Path(x) for x in (args.env_file or [])]:
        print(f"  env-file: {p} {'(exists)' if p.exists() else '(missing)'}")
    print(f"  compose consumers: {', '.join(CONSUMERS)}")
    if args.dry_run:
        print("dry-run — no changes made")
        return 0

    missing = [k for k, v in {
        "CLOUDFLARE_ACCOUNT_ID": account, "CF token": cf_tok,
        "MAIL_OUTBOUND_URL": out_url, "old secret": old}.items() if not v]
    if missing:
        print(f"FATAL missing: {', '.join(missing)}", file=sys.stderr)
        return 1

    # 1. Infisical (best-effort — .env push still keeps the stack consistent;
    #    the daily pull would otherwise revert the rotation)
    try:
        tok = infisical_token()
        infisical_upsert(args.secret, new, tok)
        step(f"infisical {INFISICAL_ENV}{INFISICAL_PATH} {args.secret} updated")
    except Exception as e:  # noqa: BLE001
        print(f"WARN infisical step failed ({e}) — continuing; "
              f"re-run infisical-sync push or update manually", file=sys.stderr)

    # 2. .env files
    touched = update_env_files(args.secret, new)
    step(f".env updated: {', '.join(touched)}")

    # 3. CF worker secret
    cf_secret_bulk(account, cf_tok, args.worker, {args.secret: new})
    step(f"CF worker {args.worker} secret patched")

    # 4. recreate consumers
    if not args.skip_compose:
        compose_up(CONSUMERS)

    # give CF's secret propagation a moment, then smoke
    time.sleep(5)
    smoke_send(out_url, new, smoke_to, args.worker)
    step(f"smoke OK — {args.worker} /send accepted new token "
         f"(sha={_fp(new)}) to {smoke_to}")
    print("ROTATION COMPLETE")
    return 0


if __name__ == "__main__":
    sys.exit(main())

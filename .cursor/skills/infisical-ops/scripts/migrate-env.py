#!/usr/bin/env python3
"""Batch-push .env credentials to Infisical (cloudless-infra/prod).

Reads KEY=VALUE pairs from a dotenv file, pushes every non-empty value whose
name matches a credential pattern. Values are never printed — only names and
HTTP status codes. Reuses the universal-auth identity from
~/.config/secrets/infisical (same as push-secret.sh).

Usage: python3 migrate-env.py [--env-file /path/.env] [--dry-run]
"""

import json
import os
import re
import sys
import urllib.request

BASE = "http://localhost:8090"
WS = "27660119-de58-4d8b-adaf-a39c8fc8c79e"
ENV = "prod"
CREDS = os.path.expanduser("~/.config/secrets/infisical")

CRED_RE = re.compile(
    r"(PASSWORD|PASSWD|SECRET|TOKEN|API_KEY|_KEY$|USERNAME|USER$|EMAIL|"
    r"COOKIE|SESSION|TOTP|CREDENTIAL|AUTH|PRIVATE)"
)
SKIP_RE = re.compile(
    r"^(CI_TEST_|_TEST_|TEST_|INFISICAL_|EMAIL_PROVIDER$|CF_EMAIL_ENABLED$|"
    r"DIGEST_EMAIL_|TOKEN_STORAGE_DB$)"
)


def _cfg(key: str) -> str:
    for line in open(CREDS):
        if line.startswith(key + "="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"missing {key} in {CREDS}")


def _post(path: str, payload: dict, token: str | None = None) -> tuple[int, dict]:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}
        | ({"Authorization": f"Bearer {token}"} if token else {}),
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def login() -> str:
    code, body = _post(
        "/api/v1/auth/universal-auth/login",
        {"clientId": _cfg("INFISICAL_UA_CLIENT_ID"),
         "clientSecret": _cfg("INFISICAL_UA_CLIENT_SECRET")},
    )
    if code != 200:
        raise SystemExit(f"login failed: {code} {body}")
    return body["accessToken"]


def push(token: str, name: str, value: str) -> int:
    payload = {
        "workspaceId": WS,
        "environment": ENV,
        "secretValue": value,
        "secretPath": "/",
        "type": "shared",
    }
    code, _ = _post(f"/api/v3/secrets/raw/{name}", payload, token)
    if code == 400:  # already exists -> PATCH
        req = urllib.request.Request(
            BASE + f"/api/v3/secrets/raw/{name}",
            data=json.dumps(payload).encode(),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            method="PATCH",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code
    return code


def parse_env(path: str) -> dict[str, str]:
    out = {}
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if v:
            out[k] = v
    return out


def main() -> None:
    env_file = "/home/tbaltzakis/cu130-slim/.env"
    dry = "--dry-run" in sys.argv
    if "--env-file" in sys.argv:
        env_file = sys.argv[sys.argv.index("--env-file") + 1]
    env = parse_env(env_file)
    keys = sorted(
        k for k in env
        if CRED_RE.search(k) and not SKIP_RE.match(k)
        and not k.startswith("INFISICAL_")
    )
    print(f"{len(keys)} credential vars in {env_file}")
    if dry:
        for k in keys:
            print(" ", k)
        return
    token = login()
    ok = fail = 0
    for k in keys:
        code = push(token, k, env[k])
        status = "ok" if code in (200, 201) else f"HTTP {code}"
        if code in (200, 201):
            ok += 1
        else:
            fail += 1
        print(f"{k} -> {status}")
    print(f"done: {ok} pushed, {fail} failed")


if __name__ == "__main__":
    main()

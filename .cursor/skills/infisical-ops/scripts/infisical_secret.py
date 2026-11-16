#!/usr/bin/env python3
"""Infisical secret CLI — non-root universal-auth path.

Replaces the root-dependent push-secret.sh / infisical-api.sh helpers for
everyday ops. Cred resolution order:
  1. INFISICAL_UA_CLIENT_ID / INFISICAL_UA_CLIENT_SECRET env vars
  2. cu130-slim/.env  (INFISICAL_UA_*)
  3. ~/.config/secrets/infisical

API base resolution order:
  1. INFISICAL_API_BASE env var
  2. http://127.0.0.1:8090  (published container port)
  3. inside the infisical container: http://127.0.0.1:8080

Usage:
  infisical_secret.py login                 # verify creds, print identity
  infisical_secret.py list [--env prod]     # key names only (never values)
  infisical_secret.py get NAME [--env prod] # print value to stdout
  infisical_secret.py set NAME FILE|-       # upsert secret (stdin or file)
  infisical_secret.py status ENVFILE        # diff .env vs Infisical (names)
  infisical_secret.py diag                  # connectivity + identity check

Never prints secret values except `get` (which is the point) — list/status
emit names/counts only.
"""

import argparse
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error

REPO_ENV = os.path.expanduser("~/cu130-slim/.env")
CREDS_FILE = os.path.expanduser("~/.config/secrets/infisical")
WORKSPACE_ID = "27660119-de58-4d8b-adaf-a39c8fc8c79e"
DEFAULT_ENV = "prod"
SECRET_PATH = "/"


def _env_file_keys(path):
    out = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    out[k.strip()] = v.strip().strip('"').strip("'")
    except OSError:
        pass
    return out


def _creds():
    cid = os.environ.get("INFISICAL_UA_CLIENT_ID")
    csec = os.environ.get("INFISICAL_UA_CLIENT_SECRET")
    if not (cid and csec):
        merged = {}
        for p in (REPO_ENV, CREDS_FILE):
            merged.update(_env_file_keys(p))
        cid = cid or merged.get("INFISICAL_UA_CLIENT_ID")
        csec = csec or merged.get("INFISICAL_UA_CLIENT_SECRET")
    if not (cid and csec):
        sys.exit("no universal-auth creds (env, .env, or ~/.config/secrets/infisical)")
    return cid, csec


def _bases():
    bases = []
    env_base = os.environ.get("INFISICAL_API_BASE")
    if env_base:
        bases.append(env_base.rstrip("/"))
    bases += ["http://127.0.0.1:8090", "http://127.0.0.1:8080"]
    return bases


def _http(method, url, body=None, token=None, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {"raw": "unparseable"}
    except Exception as e:
        return 0, {"error": str(e)}


def _login():
    cid, csec = _creds()
    last = None
    for base in _bases():
        code, resp = _http(
            "POST",
            f"{base}/api/v1/auth/universal-auth/login",
            {"clientId": cid, "clientSecret": csec},
        )
        if code == 200 and resp.get("accessToken"):
            return base, resp["accessToken"], resp
        last = (base, code, resp)
    base, code, resp = last
    msg = resp.get("message") or resp.get("error") or resp
    sys.exit(f"login failed on all bases (last {base}: HTTP {code} {msg})")


def _api(base, token, method, path, body=None):
    return _http(method, f"{base}{path}", body, token=token)


def _list_secrets(base, token, env):
    # NOTE: /api/v3/secrets/raw paginates — limit=1000 or keys go missing.
    code, resp = _api(
        base, token, "GET",
        f"/api/v3/secrets/raw?workspaceId={WORKSPACE_ID}"
        f"&environment={env}&secretPath={SECRET_PATH}&limit=1000",
    )
    if code != 200:
        sys.exit(f"list failed: HTTP {code} {resp}")
    return {s["secretKey"]: s for s in resp.get("secrets", [])}


def cmd_login(_a):
    base, tok, resp = _login()
    ident = resp.get("identity", {})
    print(f"ok base={base} identity={ident.get('name', '?')} id={ident.get('id', '?')}")


def cmd_list(a):
    base, tok, _ = _login()
    secrets = _list_secrets(base, tok, a.env)
    for k in sorted(secrets):
        print(k)
    print(f"# {len(secrets)} secrets in {a.env}", file=sys.stderr)


def cmd_get(a):
    base, tok, _ = _login()
    secrets = _list_secrets(base, tok, a.env)
    if a.name not in secrets:
        sys.exit(f"{a.name} not found in {a.env}")
    print(secrets[a.name].get("secretValue", ""))


def cmd_set(a):
    base, tok, _ = _login()
    val = sys.stdin.read() if a.src == "-" else open(a.src).read()
    val = val.rstrip("\n")
    body = {
        "workspaceId": WORKSPACE_ID,
        "environment": a.env,
        "secretPath": SECRET_PATH,
        "secretValue": val,
        "type": "shared",
    }
    code, resp = _api(base, tok, "POST", f"/api/v3/secrets/raw/{a.name}", body)
    if code == 400 and "already exist" in json.dumps(resp).lower():
        code, resp = _api(base, tok, "PATCH", f"/api/v3/secrets/raw/{a.name}", body)
    if code not in (200, 201):
        sys.exit(f"set {a.name} failed: HTTP {code} {resp}")
    print(f"{a.name} -> {code} ({a.env})")


def cmd_status(a):
    base, tok, _ = _login()
    remote = set(_list_secrets(base, tok, a.env))
    local = set(_env_file_keys(a.envfile))
    local.discard("")
    only_local = sorted(local - remote)
    only_remote = sorted(remote - local)
    both = sorted(local & remote)
    print(f"local={len(local)} remote={len(remote)} both={len(both)}")
    print(f"only-local ({len(only_local)}): {', '.join(only_local) or '-'}")
    print(f"only-remote ({len(only_remote)}): {', '.join(only_remote) or '-'}")


def cmd_diag(_a):
    cid, _ = _creds()
    print(f"clientId: {cid[:8]}…{cid[-4:]}")
    for base in _bases():
        code, resp = _http("GET", f"{base}/api/status", timeout=5)
        ok = code == 200 and resp.get("message") == "Ok"
        print(f"  {base}/api/status -> HTTP {code} {'OK' if ok else resp}")
    _login()
    print("login: OK")
    # identity row exists in DB? (postgres container, infisical db)
    try:
        out = subprocess.run(
            ["docker", "exec", "postgres", "psql",
             os.popen("grep ^INFISICAL_DB_URI= " + REPO_ENV).read().strip().split("=", 1)[1],
             "-tAc",
             'SELECT i.name FROM identities i JOIN identity_universal_auths u '
             'ON u."identityId"=i.id'],
            capture_output=True, text=True, timeout=15)
        print(f"db identities: {out.stdout.strip() or '(none)'}")
    except Exception as e:
        print(f"db check skipped: {e}")


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("login").set_defaults(fn=cmd_login)

    pl = sub.add_parser("list"); pl.add_argument("--env", default=DEFAULT_ENV)
    pl.set_defaults(fn=cmd_list)

    pg = sub.add_parser("get"); pg.add_argument("name"); pg.add_argument("--env", default=DEFAULT_ENV)
    pg.set_defaults(fn=cmd_get)

    ps = sub.add_parser("set"); ps.add_argument("name"); ps.add_argument("src")
    ps.add_argument("--env", default=DEFAULT_ENV); ps.set_defaults(fn=cmd_set)

    pst = sub.add_parser("status"); pst.add_argument("envfile", nargs="?", default=REPO_ENV)
    pst.add_argument("--env", default=DEFAULT_ENV); pst.set_defaults(fn=cmd_status)

    sub.add_parser("diag").set_defaults(fn=cmd_diag)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()

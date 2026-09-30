#!/usr/bin/env python3
"""Cloudflare API-token management — create/edit tokens programmatically.

Auth modes (auto-detected):
- CLOUDFLARE_GLOBAL_KEY + CLOUDFLARE_EMAIL — full user+account token management.
- CLOUDFLARE_ACCESS_TOKEN (or CF_TOKEN_FILE=<json with "value">) — scoped
  bearer token. Account-scope operations (list/create/edit ACCOUNT tokens)
  work when the token holds "Account API Tokens Write"; user-scope ops
  (/user/tokens) still need the Global Key.

Usage:
    python3 scripts/cf_tokens.py verify
    python3 scripts/cf_tokens.py list
    python3 scripts/cf_tokens.py perm-groups [--scope user|account] [filter]
    python3 scripts/cf_tokens.py add-perm <token-id> <perm-group-substring>
    python3 scripts/cf_tokens.py create <name> --scope account|user --perm <substr> [--perm ...]
    python3 scripts/cf_tokens.py service-token <name> [--duration forever]
    python3 scripts/cf_tokens.py roll <token-id> [--scope user|account]

New token VALUES are written to /tmp/cf-token-<name>.json (0600) — never
printed. Reads CLOUDFLARE_* from the repo-root .env.
"""

import json
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

ACCOUNT = "fb7dc7b69b662480cd5961a4d1913c78"
API = "https://api.cloudflare.com/client/v4"


def load_env() -> None:
    env = Path(__file__).resolve().parent.parent / ".env"
    for line in env.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _headers() -> dict:
    # CF_TOKEN_FILE=<path to json with "value"> overrides the bearer token —
    # used to act as a freshly-minted token (e.g. a service-token minter).
    tf = os.environ.get("CF_TOKEN_FILE")
    if tf:
        tok = json.loads(Path(tf).read_text())["value"]
        return {"Authorization": f"Bearer {tok}",
                "Content-Type": "application/json"}
    key = os.environ.get("CLOUDFLARE_GLOBAL_KEY")
    email = os.environ.get("CLOUDFLARE_EMAIL")
    if key and email:
        return {"X-Auth-Key": key, "X-Auth-Email": email,
                "Content-Type": "application/json"}
    tok = os.environ.get("CLOUDFLARE_ACCESS_TOKEN")
    if tok:
        return {"Authorization": f"Bearer {tok}",
                "Content-Type": "application/json"}
    sys.exit("no CF credentials — set CLOUDFLARE_GLOBAL_KEY+CLOUDFLARE_EMAIL "
             "or CLOUDFLARE_ACCESS_TOKEN in .env")


def using_global_key() -> bool:
    return bool(os.environ.get("CLOUDFLARE_GLOBAL_KEY")
                and not os.environ.get("CF_TOKEN_FILE"))


def cf(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        API + path, method=method, headers=_headers(),
        data=json.dumps(body).encode() if body else None)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except Exception:
            return {"success": False, "errors": [{"message": e.read().decode()[:300]}]}


def _secrets_dir() -> Path:
    # ~/.cache/cf-ops — durable across WSL sessions (this box wipes /tmp).
    d = Path.home() / ".cache" / "cf-ops"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _need_global(scope: str = "user") -> None:
    # Account-scope token ops are allowed for scoped tokens holding
    # "Account API Tokens Write" — the API enforces and 403s otherwise.
    # User-scope token ops genuinely require the Global Key / OAuth session.
    if scope == "user" and not using_global_key():
        sys.exit("user-scope token ops need CLOUDFLARE_GLOBAL_KEY+"
                 "CLOUDFLARE_EMAIL in .env")


def _find_pg(scope: str, substr: str) -> dict:
    path = ("/user/tokens/permission_groups" if scope == "user"
            else f"/accounts/{ACCOUNT}/tokens/permission_groups")
    d = cf("GET", path)
    if not d.get("success"):
        sys.exit(f"perm-groups error: {d.get('errors')}")
    matches = [g for g in d["result"]
               if substr.lower() in g.get("name", "").lower()]
    if not matches:
        sys.exit(f"no {scope} permission group matching {substr!r}")
    if len(matches) > 1:
        for g in matches:
            print("  ?", g["name"], g["id"])
        sys.exit("ambiguous — pass a more specific substring")
    return matches[0]


def _policy_for(scope: str, pg: dict) -> dict:
    res = ({"com.cloudflare.api.account." + ACCOUNT: "*"} if scope == "account"
           else {"com.cloudflare.api.account.*": "*"})
    return {"effect": "allow", "permission_groups": [{"id": pg["id"]}],
            "resources": res}


def cmd_verify() -> None:
    mode = "global-key" if using_global_key() else "scoped-token (limited)"
    print(f"auth mode: {mode}")
    r = cf("GET", "/user/tokens/verify")
    if r.get("success"):
        t = r["result"]
        print(f"user-token verify: {t.get('status')} id={t.get('id')}")
    r2 = cf("GET", f"/accounts/{ACCOUNT}/tokens/verify")
    if r2.get("success"):
        t = r2["result"]
        print(f"acct-token verify: {t.get('status')} id={t.get('id')}")
    if using_global_key():
        me = cf("GET", "/user")
        if me.get("success"):
            u = me["result"]
            print(f"global key OK — user: {u.get('email')} ({u.get('username')})")


def cmd_list() -> None:
    if using_global_key():
        d = cf("GET", "/user/tokens?per_page=100")
        if d.get("success"):
            print("── user tokens ──")
            for t in d["result"]:
                print(f"{t['id']}  {t['name']:40}  {t.get('status')}")
    d = cf("GET", f"/accounts/{ACCOUNT}/tokens?per_page=100")
    if not d.get("success"):
        sys.exit(f"account tokens error: {d.get('errors')}")
    print("── account tokens ──")
    for t in d["result"]:
        print(f"{t['id']}  {t['name']:40}  {t.get('status')}")


def cmd_perm_groups(scope: str, substr: str) -> None:
    path = ("/user/tokens/permission_groups" if scope == "user"
            else f"/accounts/{ACCOUNT}/tokens/permission_groups")
    d = cf("GET", path)
    if not d.get("success"):
        sys.exit(f"error: {d.get('errors')}")
    for g in d["result"]:
        if not substr or substr.lower() in g.get("name", "").lower():
            print(f"{g['id']}  {g.get('name')}")


def cmd_add_perm(token_id: str, substr: str) -> None:
    # try account token first, then user token
    base = f"/accounts/{ACCOUNT}/tokens/{token_id}"
    d = cf("GET", base)
    scope = "account"
    if not d.get("success"):
        _need_global("user")
        base = f"/user/tokens/{token_id}"
        d = cf("GET", base)
        scope = "user"
    if not d.get("success"):
        sys.exit(f"token {token_id} not found: {d.get('errors')}")
    tok = d["result"]
    pg = _find_pg(scope, substr)
    print(f"adding {scope} perm group: {pg['name']} ({pg['id']})")
    policies = tok.get("policies", [])
    # merge into an existing allow policy on the same resource if present,
    # else append a new policy
    merged = False
    for p in policies:
        if p.get("effect") == "allow" and p.get("resources"):
            ids = {g["id"] for g in p.get("permission_groups", [])}
            if pg["id"] not in ids:
                p.setdefault("permission_groups", []).append({"id": pg["id"]})
            merged = True
            break
    if not merged:
        policies.append(_policy_for(scope, pg))
    body = {"name": tok["name"], "status": tok.get("status", "active"),
            "policies": policies}
    r = cf("PUT", base, body)
    if not r.get("success"):
        sys.exit(f"update failed: {r.get('errors')}")
    print(f"OK — {tok['name']} now has '{pg['name']}'")


def cmd_create(name: str, scope: str, perms: list[str]) -> None:
    _need_global(scope)
    policies = [_policy_for(scope, _find_pg(scope, s)) for s in perms]
    if not policies:
        sys.exit("create needs at least one --perm <substring>")
    path = ("/user/tokens" if scope == "user"
            else f"/accounts/{ACCOUNT}/tokens")
    r = cf("POST", path, {"name": name, "policies": policies})
    if not r.get("success"):
        sys.exit(f"create failed: {r.get('errors')}")
    t = r["result"]
    out = _secrets_dir() / f"cf-token-{name}.json"
    out.write_text(json.dumps({"id": t["id"], "name": t["name"],
                               "value": t["value"]}, indent=2))
    out.chmod(0o600)
    print(f"created {t['name']} id={t['id']} → value in {out} (0600)")


def cmd_service_token(name: str, duration: str) -> None:
    body = {"name": name, "duration": duration}
    r = cf("POST", f"/accounts/{ACCOUNT}/access/service_tokens", body)
    if not r.get("success"):
        sys.exit(f"create failed: {r.get('errors')}")
    t = r["result"]
    out = _secrets_dir() / f"cf-svctoken-{name}.json"
    out.write_text(json.dumps({"id": t["id"], "name": t["name"],
                               "client_id": t["client_id"],
                               "client_secret": t["client_secret"]}, indent=2))
    out.chmod(0o600)
    print(f"created service token {t['name']} id={t['id']} → creds in {out} (0600)")


def cmd_roll(token_id: str, scope: str = "account") -> None:
    # PUT …/tokens/{id}/value rolls the secret IMMEDIATELY — the old value dies
    # even if the HTTP response errors. Parse `result` as a bare string.
    path = (f"/user/tokens/{token_id}/value" if scope == "user"
            else f"/accounts/{ACCOUNT}/tokens/{token_id}/value")
    r = cf("PUT", path, {})
    res = r.get("result")
    value = res if isinstance(res, str) else (res or {}).get("value")
    if not value:
        sys.exit(f"roll failed or value lost — check response: {r.get('errors')} "
                 "(the old value may already be dead; recover via dashboard roll)")
    out = _secrets_dir() / f"cf-token-rolled-{token_id[:8]}.json"
    out.write_text(json.dumps({"id": token_id, "value": value}, indent=2))
    out.chmod(0o600)
    print(f"rolled token {token_id[:8]}… → new value in {out} (0600)")


if __name__ == "__main__":
    load_env()
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "verify":
        cmd_verify()
    elif cmd == "list":
        cmd_list()
    elif cmd == "perm-groups":
        scope = "account"
        if "--scope" in args:
            i = args.index("--scope")
            scope = args[i + 1]
            args = args[:i] + args[i + 2:]
        cmd_perm_groups(scope, args[0] if args else "")
    elif cmd == "add-perm":
        if len(args) < 2:
            sys.exit("add-perm <token-id> <perm-group-substring>")
        cmd_add_perm(args[0], args[1])
    elif cmd == "create":
        if not args:
            sys.exit("create <name> --scope account|user --perm <substr> ...")
        name = args[0]
        scope, perms = "account", []
        i = 1
        while i < len(args):
            if args[i] == "--scope":
                scope = args[i + 1]
                i += 2
            elif args[i] == "--perm":
                perms.append(args[i + 1])
                i += 2
            else:
                i += 1
        cmd_create(name, scope, perms)
    elif cmd == "service-token":
        if not args:
            sys.exit("service-token <name> [--duration forever]")
        dur = "forever"
        if "--duration" in args:
            dur = args[args.index("--duration") + 1]
        cmd_service_token(args[0], dur)
    elif cmd == "roll":
        if not args:
            sys.exit("roll <token-id> [--scope user|account]")
        scope = "account"
        if "--scope" in args:
            i = args.index("--scope")
            scope = args[i + 1]
            args = args[:i] + args[i + 2:]
        cmd_roll(args[0], scope)
    else:
        sys.exit(__doc__)

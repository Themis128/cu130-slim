#!/usr/bin/env python3
"""cred_audit.py — drift audit for platform login credentials.

Compares every platform-cred key across three sources:
  1. SocialAuto secret store (canonical — GET /api/v1/secrets/{key})
  2. repo-root .env
  3. deploy/socialauto-standalone/.env

Values are hashed (sha256[:12]) for comparison and NEVER printed.

Usage:
  cred_audit.py            # report only
  cred_audit.py --fix-mirror   # copy store value into diverging .env files

Exit 0 = all OK, 1 = divergence/missing, 2 = infra error.
"""
from __future__ import annotations

import hashlib
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # .devin/skills/<name>/scripts -> repo root
sys.path.insert(0, str(REPO / ".devin/skills/_lib"))


def _main_checkout() -> Path:
    """Worktrees share .git but not .env — resolve the primary checkout
    (first `git worktree list` entry) so secrets resolve on any worktree."""
    try:
        import subprocess
        out = subprocess.run(
            ["git", "-C", str(REPO), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        ).stdout
        for line in out.splitlines():
            if line.startswith("worktree "):
                return Path(line.split(" ", 1)[1])
    except Exception:
        pass
    return REPO


MAIN = _main_checkout()

# skill_http.env() reads <script-repo>/.env — empty inside worktrees.
# Pre-load admin creds from the main checkout's .env into os.environ.
_env_path = MAIN / ".env"
if _env_path.exists():
    for _line in _env_path.read_text().splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))

from skill_http import social_api, request_status  # noqa: E402

# Platform login credentials — anything an agent might be tempted to
# hand-edit in .env. Infra secrets (DB passwords, API keys) are out of
# scope; they belong to Infisical.
PLATFORM_CRED_KEYS = [
    # Meta family
    "FACEBOOK_PASSWORD", "INSTAGRAM_PASSWORD",
    "META_TOTP_SECRET", "META_TOTP_SECRET_IG",
    "FACEBOOK_USERNAME", "INSTAGRAM_USERNAME",
    "FACEBOOK_APP_ID", "FACEBOOK_APP_SECRET",
    "INSTAGRAM_APP_ID", "INSTAGRAM_APP_SECRET",
    "WHATSAPP_ACCESS_TOKEN", "WHATSAPP_APP_SECRET",
    "WHATSAPP_VERIFY_TOKEN", "WHATSAPP_PHONE_NUMBER_ID",
    # LinkedIn
    "LINKEDIN_PASSWORD", "LINKEDIN_CLIENT_ID", "LINKEDIN_CLIENT_SECRET",
    # TikTok
    "TIKTOK_DEV_PASSWORD", "TIKTOK_CLIENT_KEY", "TIKTOK_CLIENT_SECRET",
    # X / Twitter
    "TWITTER_PASSWORD", "TWITTER_TOTP_SECRET",
    "X_OAUTH_CLIENT_ID", "X_OAUTH_CLIENT_SECRET",
    "X_BEARER_TOKEN", "X_ACCESS_TOKEN", "X_ACCESS_TOKEN_SECRET",
    "X_REFRESH_TOKEN", "X_WEB_AUTH_TOKEN",
    # Google (brand-account logins used by platform flows)
    "GOOGLE_PASSWORD", "GOOGLE_APP_PASSWORD",
]

# Store-only platform creds (verified present in the store 2026-11-22;
# intentionally absent from host .env — listed so they aren't flagged as
# unknown store-only keys, and so a future .env appearance gets audited).
KNOWN_STORE_ONLY_KEYS = {
    "FB_USER_TOKEN",
    "LINKEDIN_MEMBER_DP_TOKEN",
    "MESSENGER_VERIFY_TOKEN",
    "SKOOL_PASSWORD",
    "TWITTER_ACCESS_TOKEN",
    "TWITTER_ACCESS_TOKEN_SECRET",
    "TWITTER_API_SECRET",
}

ENV_FILES = {
    "root": MAIN / ".env",
    "standalone": MAIN / "deploy/socialauto-standalone/.env",
}


def _parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def _h(v: str | None) -> str:
    return "-" if not v else hashlib.sha256(v.encode()).hexdigest()[:12]


def main() -> int:
    fix = "--fix-mirror" in sys.argv
    try:
        api, tok = social_api()
    except Exception as exc:
        print(f"API login failed: {exc}", file=sys.stderr)
        return 2

    envs = {name: _parse_env(p) for name, p in ENV_FILES.items()}
    present = {k for e in envs.values() for k in e}
    keys = [k for k in PLATFORM_CRED_KEYS if k in present]

    rows = []
    for key in keys:
        code, body = request_status("GET", f"{api}/api/v1/secrets/{key}", token=tok)
        store_val = None
        if code == 200:
            try:
                import json
                store_val = json.loads(body).get("value")
            except Exception:
                store_val = None
        hashes = {"store": _h(store_val)}
        for name, e in envs.items():
            hashes[name] = _h(e.get(key))
        uniq = set(hashes.values())
        if len(uniq) == 1 and "-" not in uniq:
            status = "OK"
        elif store_val is None and "-" in hashes["store"]:
            status = "ENV-ONLY"  # not in store; env fallback resolves it
        else:
            status = "DIVERGED"
        rows.append((key, status, hashes))

    # also flag store keys that look like platform creds but aren't in env
    code, body = request_status("GET", f"{api}/api/v1/secrets", token=tok)
    store_keys = set()
    if code == 200:
        try:
            import json
            store_keys = {s.get("key") for s in json.loads(body).get("secrets", [])}
        except Exception:
            pass
    extra = sorted(k for k in store_keys
                   if re.search(r"PASSWORD|TOTP|SECRET|TOKEN", k)
                   and k not in keys and k not in PLATFORM_CRED_KEYS
                   and k not in KNOWN_STORE_ONLY_KEYS)

    # Known store-only keys: confirm they still exist in the store.
    missing_store_only = sorted(
        k for k in KNOWN_STORE_ONLY_KEYS
        if k not in store_keys and k not in present)

    bad = 0
    for key, status, h in rows:
        mark = {"OK": " ", "DIVERGED": "!", "ENV-ONLY": "~"}[status]
        if status == "DIVERGED":
            bad += 1
        print(f"{mark} {status:9s} {key:32s} store={h['store']} root={h['root']} standalone={h['standalone']}")
    if extra:
        print("\nstore-only secret-looking keys (not in audit inventory):")
        for k in extra[:30]:
            print(f"  ? {k}")
    if missing_store_only:
        bad += len(missing_store_only)
        print("\n! known store-only keys missing from store AND env:")
        for k in missing_store_only:
            print(f"  ! {k}")

    if fix:
        fixed = 0
        for key, status, h in rows:
            if status != "DIVERGED":
                continue
            code, body = request_status("GET", f"{api}/api/v1/secrets/{key}", token=tok)
            if code != 200:
                continue
            import json
            val = json.loads(body).get("value")
            if not val:
                continue
            for name, p in ENV_FILES.items():
                env = _parse_env(p)
                if env.get(key) == val:
                    continue
                lines = p.read_text().splitlines()
                done = False
                for i, l in enumerate(lines):
                    if l.startswith(f"{key}="):
                        lines[i] = f'{key}="{val}"'
                        done = True
                        break
                if not done:
                    lines.append(f'{key}="{val}"')
                p.write_text("\n".join(lines) + "\n")
                fixed += 1
        print(f"\n--fix-mirror: rewrote {fixed} .env slots to store values")

    print(f"\n{len(rows)} keys audited, {bad} diverged")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

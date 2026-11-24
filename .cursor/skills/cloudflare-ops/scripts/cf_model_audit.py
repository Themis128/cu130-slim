#!/usr/bin/env python3
"""cf_model_audit — diff repo Workers AI model constants against the live
Cloudflare catalog. Run after Cloudflare deprecation emails or before any
model-default change; exit 1 when a referenced @cf/ model is dead.

Usage:
    cf_model_audit.py            # scan app/ for @cf/ model strings, probe catalog
    cf_model_audit.py --json     # machine-readable report

Auth: CLOUDFLARE_ACCOUNT_ID + (CLOUDFLARE_WORKERS_AI_TOKEN |
CLOUDFLARE_AI_API_TOKEN | CLOUDFLARE_API_TOKEN) from repo .env.
"""
from __future__ import annotations

import argparse
import io
import os
import json
import re
import tokenize
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "social-automation" / "backend"
MODEL_RE = re.compile(r"@cf/[A-Za-z0-9._/-]+")
SKIP_DIRS = {".venv", "node_modules", ".git", "tests", "__pycache__"}


def _env() -> dict[str, str]:
    env: dict[str, str] = dict(os.environ)
    for p in (REPO / ".env", BACKEND / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(errors="replace").splitlines():
            m = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line.strip())
            if m:
                env[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return env


def _catalog(env: dict[str, str]) -> set[str]:
    acct = env.get("CLOUDFLARE_ACCOUNT_ID")
    tok = (env.get("CLOUDFLARE_WORKERS_AI_TOKEN")
           or env.get("CLOUDFLARE_AI_API_TOKEN")
           or env.get("CLOUDFLARE_API_TOKEN"))
    if not acct or not tok:
        raise SystemExit("need CLOUDFLARE_ACCOUNT_ID + a CF API token in .env")
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{acct}/ai/models/search?per_page=100",
        headers={"Authorization": f"Bearer {tok}"})
    d = json.loads(urllib.request.urlopen(req, timeout=30).read())
    result = d.get("result") or {}
    models = result.get("models") if isinstance(result, dict) else result
    if models is None:
        raise SystemExit(f"unexpected catalog shape: {str(d)[:200]}")
    return {m["name"] for m in models}


def _repo_models() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for p in BACKEND.rglob("*.py"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            raw = p.read_text(errors="replace")
        except OSError:
            continue
        # strip comments so "# retired @cf/x" notes don't flag
        try:
            text = "".join(
                tok.string for tok in tokenize.generate_tokens(io.StringIO(raw).readline)
                if tok.type != tokenize.COMMENT
            )
        except tokenize.TokenError:
            text = raw
        for m in set(MODEL_RE.findall(text)):
            # skip docstring/glob artifacts like @cf/..., @cf/foo/, @cf/x-
            if ".." in m or m.endswith(("-", "/")):
                continue
            found.setdefault(m, set()).add(str(p.relative_to(REPO)))
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    live = _catalog(_env())
    repo = _repo_models()
    dead = {m: files for m, files in repo.items() if m not in live}
    report = {
        "live_catalog_size": len(live),
        "repo_models": len(repo),
        "dead": {m: sorted(f) for m, f in dead.items()},
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"catalog: {len(live)} live models | repo references: {len(repo)}")
        if dead:
            print(f"\nDEAD ({len(dead)}):")
            for m, files in sorted(dead.items()):
                print(f"  {m}")
                for f in sorted(files)[:4]:
                    print(f"      {f}")
        else:
            print("all repo @cf/ models are live")
    return 1 if dead else 0


if __name__ == "__main__":
    sys.exit(main())

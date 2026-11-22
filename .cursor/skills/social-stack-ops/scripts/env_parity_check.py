#!/usr/bin/env python3
"""env_parity_check.py — root .env vs standalone .env divergence report.

The recurring incident class (2026-11-22): credential rotation lanes
(ig_pw_reset, meta_reset_watch, manual edits) write the repo-root
``cu130-slim/.env``, but the running fleet is compose project
``socialauto-standalone`` whose ``env_file`` is
``deploy/socialauto-standalone/.env``. The two files silently diverge —
containers keep the OLD secret while everything looks updated.

Checks only keys the standalone stack actually consumes:
  - every KEY in the standalone .env (env_file injects all of them), plus
  - every ${VAR} referenced by the standalone compose file.

Reports DIVERGED (different values in the two files — never prints
values, secrets stay secret), ROOT-ONLY (missing from standalone —
containers will inject empty), and STANDALONE-ONLY (fine, informational).

Usage:
  env_parity_check.py            # human table, exit 1 on divergence
  env_parity_check.py --json     # machine-readable
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path.home() / "cu130-slim"
STANDALONE = ROOT / "deploy" / "socialauto-standalone"
VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}")


def parse_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def compose_consumed_vars() -> set[str]:
    txt = (STANDALONE / "docker-compose.yml").read_text()
    return set(VAR_RE.findall(txt))


def main() -> int:
    root_env = parse_env(ROOT / ".env")
    sa_env = parse_env(STANDALONE / ".env")
    consumed = compose_consumed_vars()

    interesting = (set(sa_env) | consumed) & set(root_env) | consumed
    diverged = sorted(
        k for k in interesting
        if k in root_env and k in sa_env and root_env[k] != sa_env[k]
    )
    root_only = sorted(
        k for k in consumed | (set(root_env) & set(sa_env) | consumed)
        if k in root_env and k not in sa_env
    )
    sa_only = sorted(k for k in sa_env if k not in root_env)

    if "--json" in sys.argv:
        print(json.dumps(
            {"diverged": diverged, "root_only": root_only,
             "standalone_only": sa_only}, indent=2))
        return 1 if diverged or root_only else 0

    if diverged:
        print(f"DIVERGED ({len(diverged)}): same key, different value "
              "(values never printed):")
        for k in diverged:
            print(f"  {k}")
    if root_only:
        print(f"\nROOT-ONLY ({len(root_only)}): consumed by the standalone "
              "stack but missing from its .env — containers inject EMPTY:")
        for k in root_only:
            print(f"  {k}")
    if sa_only:
        print(f"\nSTANDALONE-ONLY ({len(sa_only)}): informational")
        for k in sa_only[:20]:
            print(f"  {k}")
        if len(sa_only) > 20:
            print(f"  … and {len(sa_only) - 20} more")
    if not diverged and not root_only:
        print("parity ok — no diverged or missing consumed keys")
    return 1 if diverged or root_only else 0


if __name__ == "__main__":
    sys.exit(main())

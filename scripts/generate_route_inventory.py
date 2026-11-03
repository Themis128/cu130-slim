"""Regenerate docs/api-surface-inventory.md from the live FastAPI app.

Boots the app in-process (no DB needed for the route table), groups paths
by second path segment, emits the file test_docs_surfaces.py checks against.
Run: python scripts/generate_route_inventory.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "social-automation" / "backend"))
os.chdir(ROOT / "social-automation" / "backend")

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("D1_ENABLED", "false")
os.environ.setdefault("SOCIAL_ADMIN_EMAIL", "")
os.environ.setdefault("SOCIAL_ADMIN_PASSWORD", "")

from app.main import app  # noqa: E402

routes: dict[str, set[str]] = {}
for r in app.routes:
    path = getattr(r, "path", "")
    methods = ",".join(sorted(getattr(r, "methods", []) - {"HEAD", "OPTIONS"}))
    if not path or not methods:
        continue
    seg = path.strip("/").split("/")
    group = seg[2] if len(seg) > 2 and seg[0] == "api" else "(root)"
    routes.setdefault(group, set()).add(f"- `{path}` — {methods}")

out = ["# API Surface Inventory",
       "",
       "<!-- AUTO-GENERATED from openapi.json — regenerate:",
       "",
       '  python3 scripts/generate_route_inventory.py',
       "",
       "-->",
       "",
       "Every route the FastAPI app serves, grouped by prefix. `test_docs_surfaces.py`",
       "asserts every live route appears here — regenerate this file when adding routes.",
       ""]
for group in sorted(routes):
    out.append(f"## `{group}`")
    out.append("")
    out.extend(sorted(routes[group]))
    out.append("")
Path("docs/api-surface-inventory.md").write_text("\n".join(out))
print(f"wrote {sum(len(v) for v in routes.values())} routes")

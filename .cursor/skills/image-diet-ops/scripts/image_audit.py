#!/usr/bin/env python3
"""Image-diet audit — dep reachability + image tag analysis for cu130-slim.

Two reports:

1. ``deps`` — classify every pyproject dependency as api-reachable (core) or
   worker-only (extra candidate). A dep is "api-reachable" if any file under
   app/api/, app/main.py, or a module those files import does a TOP-LEVEL
   import of it. Unguarded lazy imports inside functions reachable from api
   routes are flagged separately — those still crash slim-image endpoints.

2. ``images`` — docker image sizes for cu130-slim tags + services sharing one
   image (duplicate-tag detection).

Usage (repo root):
    python .devin/skills/image-diet-ops/scripts/image_audit.py deps
    python .devin/skills/image-diet-ops/scripts/image_audit.py images
"""
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
BACKEND = ROOT / "social-automation" / "backend"
PYPROJECT = BACKEND / "pyproject.toml"

API_DIRS = [BACKEND / "app" / "api", BACKEND / "app" / "main.py"]

# Module name ↔ dep name where they differ.
MODULE_ALIASES = {
    "PyMuPDF": ["fitz", "pymupdf"],
    "pillow-heif": ["pillow_heif"],
    "pillow-avif-plugin": ["pillow_avif"],
    "yt-dlp": ["yt_dlp"],
    "beautifulsoup4": ["bs4"],
    "linkedin-api-client": ["linkedin_api_client"],
    "facebook-business": ["facebookads", "facebook_business"],
    "pydantic-settings": ["pydantic_settings"],
    "python-dotenv": ["dotenv"],
    "requests-oauthlib": ["requests_oauthlib"],
    "httpx-oauth": ["httpx_oauth"],
    "python-multipart": ["multipart"],
    "PyJWT": ["jwt"],
    "imagehash": ["imagehash"],
    "tweety-ns": ["tweety"],
    "tiktok-private-api": ["tiktok_private_api"],
    "mcp": ["mcp"],
    "pgvector": ["pgvector"],
    "slowapi": ["slowapi"],
    "opentelemetry-sdk": ["opentelemetry.sdk"],
    "prometheus-client": ["prometheus_client"],
    "python-jose": ["jose"],
}


def _dep_name(dep: str) -> str:
    return re.split(r"[<>=!~ @\[]", dep, maxsplit=1)[0].strip()


def _modules(dep: str) -> list[str]:
    name = _dep_name(dep)
    mods = [name.lower().replace("-", "_")]
    mods += MODULE_ALIASES.get(name, [])
    return mods


def _scan(dirpath: Path, mods: list[str]) -> dict[str, list[tuple[str, int, bool]]]:
    """Return {module: [(file, line, top_level)]} for each module imported."""
    hits: dict[str, list] = {m: [] for m in mods}
    files = [dirpath] if dirpath.is_file() else dirpath.rglob("*.py")
    for f in files:
        try:
            lines = f.read_text(errors="replace").split("\n")
        except OSError:
            continue
        depth = 0
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            depth = len(line) - len(line.lstrip())
            for m in mods:
                if re.search(rf"(?:^|\s)(?:import|from)\s+{re.escape(m)}\b", stripped):
                    hits[m].append((str(f.relative_to(BACKEND)), i, depth == 0))
    return {m: v for m, v in hits.items() if v}


def deps_report() -> None:
    data = tomllib.loads(PYPROJECT.read_text())
    core = data["project"]["dependencies"]
    worker = data["project"].get("optional-dependencies", {}).get("worker", [])
    api_files = [str(p) for p in API_DIRS]

    print(f"{'dep':38} {'tier':7} api-top lazy-guarded verdict")
    print("-" * 90)
    for dep in core + worker:
        name = _dep_name(dep)
        mods = _modules(dep)
        top = lazy = 0
        for m in mods:
            if API_DIRS[0].exists():
                for f, ln, is_top in _scan(API_DIRS[0], [m]).get(m, []):
                    if is_top:
                        top += 1
                    else:
                        lazy += 1
            # whole-app scan for lazy imports (services imported by api)
            for f, ln, is_top in _scan(BACKEND / "app", [m]).get(m, []):
                if not is_top:
                    lazy += 1
        tier = "worker" if dep in worker else "core"
        if top:
            verdict = "REQUIRED-core"
        elif tier == "worker":
            verdict = "worker"
        else:
            verdict = "-> worker?"  # no api top-imports; check unguarded lazy manually
        print(f"{name:38} {tier:7} {top:>7} {lazy:>12} {verdict}")
    print("\nScan: top-level imports under app/api/ + lazy imports app-wide.")


def images_report() -> None:
    out = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}} {{.Size}}"],
        capture_output=True, text=True,
    ).stdout.strip().split("\n")
    ours = [l for l in out if "cu130-slim" in l or l.startswith("mautic") or "n8n" in l]
    print(f"{'image':70} size")
    print("-" * 80)
    for l in sorted(ours):
        repo, _, size = l.rpartition(" ")
        print(f"{repo:70} {size}")

    # services → image map from compose
    cfg = subprocess.run(
        ["docker", "compose", "config", "--format", "json"],
        capture_output=True, text=True, cwd=ROOT,
    )
    if cfg.returncode == 0:
        import json
        imgs: dict[str, list[str]] = {}
        for n, s in json.loads(cfg.stdout)["services"].items():
            img = s.get("image")
            if img and "cu130-slim" in img:
                imgs.setdefault(img, []).append(n)
        print("\nshared images:")
        for img, svcs in imgs.items():
            tag = img.split(":")[-1]
            mark = "  SHARED" if len(svcs) > 1 else ""
            print(f"  {tag:42} -> {', '.join(sorted(svcs))}{mark}")


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "deps"
    (deps_report if mode == "deps" else images_report)()

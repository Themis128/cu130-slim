#!/usr/bin/env python3
"""test_const_drift — find stale constants in tests BEFORE CI does.

Scans tests/ for literal values that are known-dead or retired:
- @cf/ Workers AI model IDs not in the live catalog (reuses
  cloudflare-ops/scripts/cf_model_audit.py when present)
- Recently-retired model IDs hardcoded in assertions

Run after any model/constant migration, or proactively before pushing.
Exit 1 when drift found.

Usage:
    test_const_drift.py            # scan backend tests
    test_const_drift.py --json
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]
BACKEND = REPO / "social-automation" / "backend"
TESTS = BACKEND / "tests"
MODEL_RE = re.compile(r"@cf/[A-Za-z0-9._/-]+")
AUDIT = REPO / ".devin" / "skills" / "cloudflare-ops" / "scripts" / "cf_model_audit.py"

# Known-retired IDs — flag even if the catalog probe is unreachable.
RETIRED = {
    "@cf/meta/llama-3.2-1b-instruct",
    "@cf/meta/llama-3.2-3b-instruct",
    "@cf/meta/llama-3.1-8b-instruct",
    "@cf/meta/llama-3.1-8b-instruct-fp8",
    "@cf/openai/whisper-tiny-en",
    "@cf/runwayml/stable-diffusion-v1-5-img2img",
    "@cf/stabilityai/stable-diffusion-xl-base-1.0",
    "@cf/baai/baseten-lightning-iris",
    "@cf/facebook/wav2vec2-base-960h",
    "@cf/speechbrain/asr-cnn-transformer",
}


def _live_catalog() -> set[str] | None:
    if not AUDIT.exists():
        return None
    spec = importlib.util.spec_from_file_location("cf_model_audit", AUDIT)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod._catalog(mod._env())
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    live = _live_catalog()
    hits: dict[str, list[str]] = {}
    for p in TESTS.rglob("*.py"):
        try:
            text = p.read_text(errors="replace")
        except OSError:
            continue
        for m in set(MODEL_RE.findall(text)):
            if ".." in m or m.endswith(("-", "/")):
                continue
            dead = m in RETIRED or (live is not None and m not in live)
            if dead:
                hits.setdefault(m, []).append(str(p.relative_to(REPO)))

    report = {"catalog_checked": live is not None, "drift": {k: sorted(v) for k, v in hits.items()}}
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        if hits:
            print(f"STALE test constants ({len(hits)}):")
            for m, files in sorted(hits.items()):
                tag = "retired" if m in RETIRED else "not-in-catalog"
                print(f"  {m}  [{tag}]")
                for f in files[:5]:
                    print(f"      {f}")
        else:
            print("no stale @cf/ constants in tests" + ("" if live is not None else " (catalog unreachable — retired-list only)"))
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())

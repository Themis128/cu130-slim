#!/usr/bin/env python3
"""Reconcile .env DMR_*_MODEL refs against models actually pulled in DMR.

Reads the DMR_*_MODEL keys from the repo .env, normalizes them the same
way app/services/dmr.py does (lowercase, hf.co→huggingface.co, docker.io/
stripped), and compares them to /engines/v1/models. Each ref is classified:

  OK       — a pulled model matches the ref
  PULLABLE — missing, but pullable via `docker model pull`
             (ai/*, hf.co/*, docker.io/ai/*, huggingface.co/*)
  CUSTOM   — missing and NOT pullable (e.g. cloudless/* repacks, local GGUF)
             — needs `docker model package` or a config change, alert-only

Motivation (2026-11-12): the Ollama→DMR migration left .env pointing at
refs that were never pulled; every call failed "model not found" and
reports stalled on Cloudflare fallback. The home-ops dmr-endpoint-watch
cron auto-pulls one missing ref per run; this script is the repo-side
equivalent for manual/CI use.

Usage:
  dmr-env-check.py            # status table (read-only)
  dmr-env-check.py --pull     # sequentially pull missing PULLABLE refs
  dmr-env-check.py --probe    # + minimal inference call per resolved model
"""

import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import CHAT_BASE, DMR_BASE, dmr_get, dmr_post  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
ENV_FILE = REPO_ROOT / ".env"

MODEL_KEYS = (
    "DMR_MODEL", "DMR_MID_MODEL", "DMR_CHATBOT_MODEL", "DMR_TEXT_MODEL",
    "DMR_TINY_MODEL", "DMR_EMBEDDING_MODEL", "DMR_EMBED_MODEL",
    "DMR_QUALITY_MODEL", "DMR_MEDIA_MODEL",
)
PULLABLE_PREFIXES = ("ai/", "hf.co/", "docker.io/ai/", "huggingface.co/")


def _norm(ref: str) -> str:
    n = ref.strip().lower().replace("hf.co/", "huggingface.co/")
    n = n.removeprefix("docker.io/")
    return n.removesuffix(":latest")


def _env_models() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV_FILE.exists():
        return out
    for line in ENV_FILE.read_text().splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m and m.group(1) in MODEL_KEYS and m.group(2).strip():
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _pulled_models() -> set[str] | None:
    raw = dmr_get("/engines/v1/models")
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    ids = [m.get("id", "") for m in data.get("data", [])]
    return {_norm(i) for i in ids}


def _pullable(ref: str) -> bool:
    low = ref.strip().lower()
    return low.startswith(PULLABLE_PREFIXES)


def _pull(ref: str) -> bool:
    print(f"  pulling {ref} (this can take a while — GB-scale download)")
    p = subprocess.run(["docker", "model", "pull", ref])
    return p.returncode == 0


def _probe(model: str, kind: str) -> str:
    if kind == "embed":
        r = dmr_post("/v1/embeddings", {
            "model": model, "input": "ping", })
    else:
        r = dmr_post(f"{CHAT_BASE}/chat/completions", {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with: ok"}],
            "max_tokens": 8, "stream": False}, timeout=180)
    if isinstance(r, dict) and r.get("error"):
        return f"FAIL {str(r['error'])[:80]}"
    return "ok"


def main() -> int:
    do_pull = "--pull" in sys.argv
    do_probe = "--probe" in sys.argv

    env_models = _env_models()
    if not env_models:
        print(f"no DMR_*_MODEL refs found in {ENV_FILE}")
        return 1

    pulled = _pulled_models()
    if pulled is None:
        print("DMR API unreachable (direct + container fallback)")
        return 1

    rows = []
    for key, ref in env_models.items():
        n = _norm(ref)
        if n in pulled or any(p == n or p.startswith(n + ":") for p in pulled):
            status = "OK"
        elif _pullable(ref):
            status = "PULLABLE"
        else:
            status = "CUSTOM"
        rows.append((key, ref, status))
        if status == "PULLABLE" and do_pull:
            if _pull(ref):
                rows[-1] = (key, ref, "OK (pulled)")
            else:
                rows[-1] = (key, ref, "PULL-FAILED")

    w = max(len(k) for k, _, _ in rows)
    for key, ref, status in rows:
        print(f"{key:<{w}}  {status:<14}  {ref}")

    missing_pullable = [r for _, r, s in rows if s == "PULLABLE"]
    missing_custom = [r for _, r, s in rows if s == "CUSTOM"]
    failed = [r for _, r, s in rows if s == "PULL-FAILED"]

    print()
    if missing_pullable and not do_pull:
        print(f"{len(missing_pullable)} pullable ref(s) missing — rerun with --pull")
    if missing_custom:
        print(f"{len(missing_custom)} custom ref(s) missing — cannot docker-pull; "
              "repackage via `docker model package` or repoint the env var")
        for r in missing_custom:
            print(f"  custom: {r}")
    if failed:
        print(f"{len(failed)} pull(s) failed — check `docker model logs` / rerun")
    if do_probe:
        print("\nprobe:")
        for key, ref, status in rows:
            if not status.startswith("OK"):
                continue
            kind = "embed" if "EMBED" in key else "chat"
            print(f"  {key}: {_probe(ref, kind)}")

    bad = missing_pullable or missing_custom or failed
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())

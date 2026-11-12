#!/usr/bin/env python3
"""Detect DMR configuration drift across the three surfaces that caused
the 2026-11-12 gpu:dmr_lock incident:

  1. .env DMR_*_MODEL values vs the canonical fleet (config.py defaults)
  2. container env (baked at `compose up` time) vs the .env file —
     env_file vars are injected at container CREATE, so editing .env
     without `docker compose up -d` leaves stale values running
  3. wedged "Loading..." entries in `docker model ps` (queued loads that
     never finish — callers churn the gpu:dmr_lock until they time out)

Exit 0 = clean, 1 = drift/wedge found. --fix prints the remediation
commands (it never mutates anything itself).
"""
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # <repo>/.devin|cursor/skills/<skill>/scripts/
ENV_FILE = REPO / ".env"
CONFIG_PY = REPO / "social-automation" / "backend" / "app" / "core" / "config.py"
CONTAINER = "social-api"

# Canonical fleet — mirrors app/core/config.py DMR_* defaults and the
# docker-model-runner SKILL.md model table. NOTE: docker-compose.yml
# injects `${DMR_X:-<default>}` — an EMPTY .env value falls through to
# the compose default (smollm2 emergency values), so .env must carry
# explicit canonical refs, not blanks. DMR_MEDIA_MODEL's compose default
# (cloudless/qwen3-4b-media) is not pulled on this host — the 4B base is
# the effective canonical until the LoRA is packaged.
CANONICAL = {
    "DMR_MODEL": "ai/qwen3:8b-q4_K_M",
    "DMR_MID_MODEL": "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
    "DMR_TEXT_MODEL": "ai/qwen3:8b-q4_K_M",
    "DMR_CHATBOT_MODEL": "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
    "DMR_TINY_MODEL": "ai/smollm3",
    "DMR_QUALITY_MODEL": "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
    "DMR_EMBEDDING_MODEL": "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF",
    "DMR_EMBED_MODEL": "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF",
    "DMR_MEDIA_MODEL": "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
    "DMR_VISION_MODEL": "ai/qwen3-vl",
}
# Models that must be pulled for the canonical fleet to serve.
EXPECTED_MODELS = [
    "ai/qwen3:8b-q4_K_M",
    "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
    "ai/smollm3",
    "ai/qwen3-vl",
    "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF",
]


def sh(cmd: list[str], timeout: int = 30) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True,
                              timeout=timeout).stdout
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def parse_env(path: Path) -> dict[str, str]:
    out = {}
    if not path.exists():
        return out
    for line in path.read_text(errors="replace").splitlines():
        m = re.match(r"^([A-Z0-9_]+)=(.*)$", line.strip())
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def container_env() -> dict[str, str]:
    raw = sh(["docker", "exec", CONTAINER, "env"])
    out = {}
    for line in raw.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            out[k] = v
    return out


def docker_model_list() -> str:
    return sh(["docker", "model", "list"]).lower()


def docker_model_ps() -> str:
    return sh(["docker", "model", "ps"])


def main() -> int:
    problems: list[str] = []
    fixes: list[str] = []

    env_vals = parse_env(ENV_FILE)
    print("== .env DMR_*_MODEL vs canonical ==")
    env_dirty = False
    for key, canon in CANONICAL.items():
        actual = env_vals.get(key)
        if actual is None:
            continue  # unset -> code default applies
        mark = "OK " if actual == canon else "DRIFT"
        if actual != canon:
            env_dirty = True
            problems.append(f".env {key}={actual!r} (canonical: {canon!r})")
        print(f"  {mark} {key}={actual!r}")

    print("\n== container env vs canonical (runtime truth) ==")
    cent = container_env()
    if not cent:
        print(f"  ? cannot read env from {CONTAINER} (down?) — skipping")
    else:
        stale = []
        for key, canon in CANONICAL.items():
            cont_v = cent.get(key, "")
            # container env is baked at `compose up` — env_file edits and
            # compose-default fixes only land on recreate
            if cont_v != canon:
                stale.append(key)
                problems.append(
                    f"container {key}={cont_v!r} (canonical: {canon!r}) — needs recreate")
        if stale:
            print(f"  STALE {sorted(stale)}")
            fixes.append(
                "cd ~/cu130-slim && docker compose up -d "
                "social-api social-worker-publishing social-worker-media "
                "social-worker-default social-worker-messenger celery-beat")
        else:
            print("  OK container env matches canonical fleet")

    print("\n== pulled models vs expected fleet ==")
    models = docker_model_list()
    missing = []
    for e in EXPECTED_MODELS:
        n = e.lower().replace("hf.co/", "huggingface.co/")
        tail = n.split("/")[-1].split(":")[0]
        # match on model id tail (docker list shows short names for ai/*)
        short = e.split("/")[-1].split(":")[0].lower()
        if n in models or tail in models or short in models or e.lower() in models:
            print(f"  OK       {e}")
        else:
            missing.append(e)
            problems.append(f"model not pulled: {e}")
            print(f"  MISSING  {e}")
    if missing:
        fixes.append("docker model pull " + " && docker model pull ".join(missing))

    print("\n== wedged Loading... entries ==")
    ps = docker_model_ps()
    loading = [l for l in ps.splitlines()[1:] if "Loading" in l]
    if loading:
        for l in loading:
            print(f"  STUCK    {l.strip()}")
        problems.append(f"{len(loading)} model(s) stuck in Loading state")
        fixes.append(
            "queued loads survive `docker model unload` — if they persist "
            ">10min with RAM free, restart Docker Desktop "
            "(docker desktop restart) or wait for caller timeouts")
    else:
        print("  OK no Loading entries")

    print()
    if problems:
        print(f"DRIFT: {len(problems)} finding(s)")
        for p in problems:
            print(f"  - {p}")
        if "--fix" in sys.argv:
            print("\nRemediation:")
            for f in fixes:
                print(f"  $ {f}")
        return 1
    print("CLEAN: DMR env, container env, models, and runner state all consistent")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Apply the canonical runtime configs for all SocialAuto DMR models.
Configs live in runner memory only — WIPED on every runner restart.
Usage: dmr-configure.py [show]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import docker_model  # noqa: E402

MODELS = [
    "ai/qwen3:8b-q4_K_M",
    "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
    "ai/smollm3",
]
if len(sys.argv) > 1 and sys.argv[1] == "show":
    for m in MODELS:
        print(f"=== {m} ===")
        docker_model("configure", "show", m)
    sys.exit(0)

print("Applying canonical DMR model configs (RTX 3070 8GB)...")
docker_model("configure", "--context-size", "6144", "--keep-alive", "5m",
             "ai/qwen3:8b-q4_K_M")
docker_model("configure", "--context-size", "4096", "--keep-alive", "30m",
             "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M")
docker_model("configure", "--context-size", "4096", "--keep-alive", "5m",
             "ai/smollm3", "--", "--reasoning-budget", "0")
print("Done. Verify with: docker model configure show <model>")

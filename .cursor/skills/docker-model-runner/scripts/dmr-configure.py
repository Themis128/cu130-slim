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
    "ai/llama3.2",
    "ai/qwen3-vl",
    "ai/qwen3-embedding",
    "ai/smollm3",
]
if len(sys.argv) > 1 and sys.argv[1] == "show":
    for m in MODELS:
        print(f"=== {m} ===")
        docker_model("configure", "show", m)
    sys.exit(0)

print("Applying canonical DMR model configs (RTX 3070 8GB)...")
# Keep-alive tiers (single 8GB card — keep_alive IS the eviction policy,
# DMR does not evict on VRAM pressure, only idle timeout / slot pressure):
#   4b   30m  — pinned: latency-critical chatbot + short-form copy (~2.7GB)
#   8b   2m   — long-form/schema bursts (~5.5GB, evicts/resident-swaps)
#   llama3.2 2m, ctx 4096 — carousel copy/NLP, co-resides with 4b (~2.2GB)
#   vl   90s  — vision QA bursts (~5GB)
#   emb  60s  — embedding bursts (~1.2GB)
#   smollm3 60s, ctx 2048 — <200-char prompts (~2GB)
docker_model("configure", "--context-size", "6144", "--keep-alive", "2m",
             "ai/qwen3:8b-q4_K_M")
docker_model("configure", "--context-size", "4096", "--keep-alive", "30m",
             "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M")
docker_model("configure", "--context-size", "4096", "--keep-alive", "2m",
             "ai/llama3.2")
docker_model("configure", "--context-size", "4096", "--keep-alive", "90s",
             "ai/qwen3-vl")
docker_model("configure", "--keep-alive", "60s",
             "ai/qwen3-embedding")
docker_model("configure", "--context-size", "2048", "--keep-alive", "60s",
             "ai/smollm3", "--", "--reasoning-budget", "0")
print("Done. Verify with: docker model configure show <model>")

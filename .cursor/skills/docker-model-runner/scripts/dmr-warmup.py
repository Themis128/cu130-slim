#!/usr/bin/env python3
"""Warm the two hot-path models so first user request is fast."""
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_post  # noqa: E402


def warm(model: str) -> None:
    print(f"Warming {model} ...")
    d = dmr_post("/engines/llama.cpp/v1/chat/completions",
                 {"model": model,
                  "messages": [{"role": "user", "content": "Hi"}],
                  "max_tokens": 3}, timeout=90)
    print("  FAILED" if d.get("error") and len(d) == 1 else "  loaded")


warm("hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M")
warm("ai/qwen3:8b-q4_K_M")
print()
if shutil.which("nvidia-smi"):
    out = subprocess.run(["nvidia-smi", "--query-gpu=memory.used",
                          "--format=csv,noheader"],
                         capture_output=True, text=True).stdout.strip()
    print(f"VRAM used: {out}")

#!/usr/bin/env python3
"""Check all SocialAuto-expected DMR models are pulled."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import dmr_get  # noqa: E402

EXPECTED = [
    "ai/qwen3:8b-q4_K_M",
    "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M",
    "ai/smollm3",
    "ai/qwen3-vl",
    "hf.co/Qwen/Qwen3-Embedding-0.6B-GGUF",
]
raw = dmr_get("/engines/v1/models")
if raw is None:
    print("DMR API offline")
    sys.exit(1)
models = raw.lower()
missing = 0
for e in EXPECTED:
    n = e.lower().replace("hf.co/", "huggingface.co/")
    if n.split("/")[-1] in models or n in models:
        print(f"  OK       {e}")
    else:
        print(f"  MISSING  {e}")
        missing += 1
print()
print("All expected models present" if missing == 0 else f"{missing} missing")
sys.exit(0 if missing == 0 else 1)

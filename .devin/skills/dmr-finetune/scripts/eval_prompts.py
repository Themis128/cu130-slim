#!/usr/bin/env python3
"""A/B eval: base DMR_MID_MODEL vs the fine-tuned media model.

Sends the same terse topics through expand_visual_prompt's real system
prompt on both models and prints the outputs side by side for review.
Run on the host (needs DMR reachable at DMR_BASE).
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

DMR_BASE = os.environ.get("DMR_BASE", "http://localhost:12435")
BASE_MODEL = "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"
TUNED_MODEL = os.environ.get("DMR_MEDIA_MODEL", "cloudless/qwen3-4b-media:latest")

SYSTEM = (
    "You expand terse visual descriptions into detailed generation prompts "
    "for a diffusion model that renders a short video clip. Rules: one dense "
    "sentence or two; concrete subject, setting, lighting, color, "
    "composition, and camera motion; no text/letters/watermarks; no "
    "meta-commentary; output the prompt only."
)
# Abstract topics = OOD smoke test only. The real input distribution is the
# prompts stored in media_assets — pass --topics-file data/real_prompts.json
# to eval in-distribution.
TOPICS = [
    "cloud bill sticker shock",
    "self-hosted home lab",
    "social media automation",
    "fixed pricing relief",
    "deploy friday",
]


def topics() -> list:
    if "--topics-file" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--topics-file") + 1])
        data = json.loads(path.read_text())
        prompts = [e.get("prompt", "") for e in data if e.get("prompt")]
        n = int(os.environ.get("EVAL_N", "8"))
        return prompts[:n]
    return TOPICS


def chat(model: str, topic: str) -> str:
    body = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            # production call_dmr_chat appends /no_think for qwen3 models
            {"role": "user", "content": topic + " /no_think"},
        ],
        # production sampling per Qwen model card (top_p 0.8, top_k 20)
        "max_tokens": 160, "temperature": 0.7, "top_p": 0.8, "top_k": 20,
    }).encode()
    req = urllib.request.Request(
        f"{DMR_BASE}/engines/llama.cpp/v1/chat/completions",
        data=body, headers={"Content-Type": "application/json"})
    try:
        r = urllib.request.urlopen(req, timeout=240)
        return json.load(r)["choices"][0]["message"]["content"].strip()
    except Exception as e:  # noqa: BLE001
        return f"<error: {e}>"


for topic in topics():
    print("=" * 78)
    print("TOPIC:", topic)
    print("-" * 78)
    print("BASE :", chat(BASE_MODEL, topic))
    print()
    print("TUNED:", chat(TUNED_MODEL, topic))
    print()

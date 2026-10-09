#!/usr/bin/env python3
"""A/B eval: base DMR_MID_MODEL vs the fine-tuned media model.

Sends the same terse topics through expand_visual_prompt's real system
prompt on both models and prints the outputs side by side for review.
Run on the host (needs DMR reachable at DMR_BASE).
"""
import json
import os
import urllib.request

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
TOPICS = [
    "cloud bill sticker shock",
    "self-hosted home lab",
    "social media automation",
    "fixed pricing relief",
    "deploy friday",
]


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


for topic in TOPICS:
    print("=" * 78)
    print("TOPIC:", topic)
    print("-" * 78)
    print("BASE :", chat(BASE_MODEL, topic))
    print()
    print("TUNED:", chat(TUNED_MODEL, topic))
    print()

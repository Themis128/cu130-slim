#!/usr/bin/env python3
"""Generate an AI image and save to media library.
Uses the app's fallback chain: Local Diffusers (SD 1.5, GPU) -> Cloudflare Workers AI.
Usage: generate-image.py "prompt text" [--steps N] [--width W] [--height H] [--negative "..."]"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("prompt")
p.add_argument("--steps", type=int, default=25)
p.add_argument("--width", type=int, default=1024)
p.add_argument("--height", type=int, default=1024)
p.add_argument("--negative", default="")
a = p.parse_args()

api, token = social_api()
opts = {"width": a.width, "height": a.height, "steps": a.steps, "cfg_scale": 7.5}
if a.negative:
    opts["negative_prompt"] = a.negative
print(f"Generating image (Local Diffusers primary, CF fallback): {a.prompt}")
d = request("POST", f"{api}/api/v1/media/generate-image", token=token,
            data={"prompt": a.prompt, "options": opts}, timeout=300)
print(f'Media ID: {d.get("id", "?")}')
print(f'Filename: {d.get("filename", "?")}')
md = d.get("meta_data") or {}
print(f'Provider: {md.get("inference_provider", "?")}')
print(f'Model: {md.get("inference_model", "?")}')
qs = md.get("quality_score") or {}
if qs:
    print(f'Quality: overall={qs.get("overall")}/100 sharp={qs.get("sharpness")} '
          f'bright={qs.get("brightness")} contrast={qs.get("contrast")}')
    if md.get("quality_failed"):
        print("Quality: FLAGGED (below threshold)")

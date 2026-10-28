#!/usr/bin/env python3
"""Preview platform-aware model routing (mirrors _select_model_by_complexity
in app/services/dmr.py — does NOT send a request).
Usage: dmr-route.py [platform] [prompt-length] [--schema]"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import env  # noqa: E402

platform = sys.argv[1] if len(sys.argv) > 1 else ""
plen = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0
schema = "--schema" in sys.argv

text = env("DMR_TEXT_MODEL") or "ai/qwen3:8b-q4_K_M"
mid = env("DMR_MID_MODEL") or "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M"
tiny = env("DMR_TINY_MODEL") or "ai/smollm3"

if platform in ("instagram", "tiktok", "twitter", "x", "threads",
                "youtube", "pinterest"):
    print(f"{platform} → {mid}  (mid 4B instruct — short-form platform)")
    sys.exit(0)
if platform in ("linkedin", "facebook", "blog", "article"):
    print(f"{platform} → {text}  (text 8B — long-form platform)")
    sys.exit(0)

if schema:
    print(f"(no platform)+schema → {text}  (text 8B — schema/JSON)")
elif plen < 200:
    print(f"(no platform) short prompt → {tiny}  (tiny smollm3)")
else:
    print(f"(no platform) long prompt → {text}  (text 8B — default)")

print(f"""
All platforms:
  linkedin,facebook,blog    → {text}
  instagram,tiktok,x,threads,youtube → {mid}
  <200-char prompts         → {tiny}
  chatbots (override)       → {mid}  (DMR_CHATBOT_MODEL)""")

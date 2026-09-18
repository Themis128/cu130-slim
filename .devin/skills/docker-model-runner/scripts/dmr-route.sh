#!/usr/bin/env bash
# Preview platform-aware model routing (mirrors _select_model_by_complexity
# in app/services/dmr.py — does NOT send a request).
# Usage: dmr-route.sh [platform] [prompt-length] [--schema]
set -euo pipefail

PLATFORM="${1:-}"
PLEN="${2:-0}"
SCHEMA="${3:-}"

TEXT="${DMR_TEXT_MODEL:-ai/qwen3:8b-q4_K_M}"
MID="${DMR_MID_MODEL:-hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M}"
TINY="${DMR_TINY_MODEL:-ai/smollm3}"

case "$PLATFORM" in
    instagram|tiktok|twitter|x|threads|youtube|pinterest)
        echo "$PLATFORM → $MID  (mid 4B instruct — short-form platform)"; exit 0;;
    linkedin|facebook|blog|article)
        echo "$PLATFORM → $TEXT  (text 8B — long-form platform)"; exit 0;;
esac
if [[ "$SCHEMA" == "--schema" ]]; then
    echo "(no platform)+schema → $TEXT  (text 8B — schema/JSON)"
elif [[ "$PLEN" -lt 200 ]]; then
    echo "(no platform) short prompt → $TINY  (tiny smollm3)"
else
    echo "(no platform) long prompt → $TEXT  (text 8B — default)"
fi
echo ""
echo "All platforms:"
echo "  linkedin,facebook,blog    → $TEXT"
echo "  instagram,tiktok,x,threads,youtube → $MID"
echo "  <200-char prompts         → $TINY"
echo "  chatbots (override)       → $MID  (DMR_CHATBOT_MODEL)"

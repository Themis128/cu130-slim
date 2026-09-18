#!/usr/bin/env bash
# Apply the canonical runtime configs for all SocialAuto DMR models.
# Configs live in runner memory only — they are WIPED on every runner
# restart, so re-run this after `docker restart docker-model-runner`
# (the dmr-watchdog service does this automatically).
#
# Usage: dmr-configure.sh            # apply canonical configs
#        dmr-configure.sh show       # show all current configs
set -euo pipefail

if [[ "${1:-}" == "show" ]]; then
    for m in ai/qwen3:8b-q4_K_M "hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M" ai/smollm3; do
        echo "=== $m ==="
        docker model configure show "$m" 2>/dev/null || echo "(no config)"
    done
    exit 0
fi

echo "Applying canonical DMR model configs (RTX 3070 8GB)..."

# Long-form + schema model — unpinned between content bursts
docker model configure --context-size 6144 --keep-alive 5m ai/qwen3:8b-q4_K_M

# Mid-tier instruct — chatbots + short-form platforms, pinned warm.
# ctx MUST be explicit: the GGUF advertises 262144 ctx → 36GB KV OOM.
docker model configure --context-size 4096 --keep-alive 30m hf.co/unsloth/Qwen3-4B-Instruct-2507-GGUF:Q4_K_M

# Tiny model — reasoning off for direct content on short prompts
docker model configure --context-size 4096 --keep-alive 5m ai/smollm3 -- --reasoning-budget 0

echo "Done. Verify with: docker model configure show <model>"

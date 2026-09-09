#!/usr/bin/env bash
# Benchmark a DMR model's performance
# Usage: dmr-bench.sh <model> [concurrency] [duration]
# Example: dmr-bench.sh ai/qwen3:8b-q4_K_M 1,2,4 30s
set -euo pipefail

MODEL="${1:?Usage: dmr-bench.sh <model> [concurrency] [duration]}"
CONCURRENCY="${2:-1,2,4,8}"
DURATION="${3:-30s}"

echo "Benchmarking: $MODEL"
echo "Concurrency: $CONCURRENCY"
echo "Duration: $DURATION"
echo ""

docker model bench --json --concurrency "${CONCURRENCY//,/ }" --duration "$DURATION" "$MODEL" 2>&1 | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    print(json.dumps(d, indent=2))
except: pass
'

#!/usr/bin/env bash
# Fetch DMR logs
# Usage: dmr-logs.sh [--no-engines] [lines]
# Example: dmr-logs.sh 50        # Last 50 lines
#          dmr-logs.sh --no-engines 100
set -euo pipefail

NO_ENGINES=""
LINES=50

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-engines) NO_ENGINES="--no-engines"; shift ;;
        *) LINES="$1"; shift ;;
    esac
done

echo "=== DMR Logs (last $LINES lines) ==="
echo ""

docker model logs $NO_ENGINES 2>&1 | tail -n "$LINES"

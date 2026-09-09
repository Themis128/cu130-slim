#!/usr/bin/env bash
# Search for models on Docker Hub and HuggingFace
# Usage: dmr-search.sh [query] [source] [limit]
# Examples:
#   dmr-search.sh                    # List all available models
#   dmr-search.sh llama              # Search for "llama"
#   dmr-search.sh qwen huggingface 10 # Search HuggingFace for "qwen", 10 results
set -euo pipefail

QUERY="${1:-}"
SOURCE="${2:-all}"
LIMIT="${3:-32}"

echo "Searching: '$QUERY' (source: $SOURCE, limit: $LIMIT)"
echo ""

docker model search --json --source "$SOURCE" --limit "$LIMIT" ${QUERY:+$QUERY} 2>&1 | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
    if isinstance(d, list):
        for m in d:
            name = m.get("Name", "?")
            desc = m.get("Description", "")[:60]
            dl = m.get("Downloads", 0)
            src = m.get("Source", "?")
            size = m.get("Size", 0)
            size_mb = size / (1024*1024) if size > 0 else 0
            print(f"  {name} [{src}] downloads={dl} size={size_mb:.0f}MB")
            if desc:
                print(f"    {desc}")
        print(f"\nTotal: {len(d)} models")
    else:
        print(json.dumps(d, indent=2))
except Exception as e:
    print(f"Error parsing: {e}", file=sys.stderr)
'

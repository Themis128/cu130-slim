#!/usr/bin/env python3
"""Search for models on Docker Hub and HuggingFace.
Usage: dmr-search.py [query] [source] [limit]"""
import json
import subprocess
import sys

query = sys.argv[1] if len(sys.argv) > 1 else ""
source = sys.argv[2] if len(sys.argv) > 2 else "all"
limit = sys.argv[3] if len(sys.argv) > 3 else "32"

print(f"Searching: '{query}' (source: {source}, limit: {limit})\n")
cmd = ["docker", "model", "search", "--json", "--source", source,
       "--limit", limit]
if query:
    cmd.append(query)
r = subprocess.run(cmd, capture_output=True, text=True)
try:
    d = json.loads(r.stdout)
    if isinstance(d, list):
        for m in d:
            size_mb = (m.get("Size", 0) or 0) / (1024 * 1024)
            print(f'  {m.get("Name", "?")} [{m.get("Source", "?")}] '
                  f'downloads={m.get("Downloads", 0)} size={size_mb:.0f}MB')
            desc = (m.get("Description") or "")[:60]
            if desc:
                print(f"    {desc}")
        print(f"\nTotal: {len(d)} models")
    else:
        print(json.dumps(d, indent=2))
except Exception as e:
    print(f"Error parsing: {e}", file=sys.stderr)

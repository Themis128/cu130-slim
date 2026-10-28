#!/usr/bin/env python3
"""Fail when rendered Compose services publish the same host port."""

from __future__ import annotations

import json
import sys
from collections import defaultdict


config = json.load(sys.stdin)
published_by_port: defaultdict[str, list[str]] = defaultdict(list)

for service_name, service in config.get("services", {}).items():
    for port in service.get("ports", []):
        published = port.get("published")
        if published is not None:
            published_by_port[str(published)].append(service_name)

duplicates = {
    port: services
    for port, services in published_by_port.items()
    if len(services) > 1
}

# region agent log
try:
    import time
    from pathlib import Path

    _dbg = {
        "sessionId": "d5a1cf",
        "runId": "post-fix",
        "hypothesisId": "A",
        "location": "scripts/check-compose-ports.py",
        "message": "compose published-port scan",
        "timestamp": int(time.time() * 1000),
        "data": {
            "duplicate_count": len(duplicates),
            "duplicates": {k: v for k, v in duplicates.items()},
            "messenger_ports": published_by_port.get("9230", [])
            + published_by_port.get("9229", []),
            "port_9229": published_by_port.get("9229", []),
            "port_9230": published_by_port.get("9230", []),
        },
    }
    Path("/home/tbaltzakis/cu130-slim/.cursor/debug-d5a1cf.log").open("a").write(
        json.dumps(_dbg) + "\n"
    )
except Exception:
    pass
# endregion

if duplicates:
    print("Duplicate published host ports detected:", file=sys.stderr)
    for port, services in sorted(duplicates.items()):
        print(f"  {port}: {', '.join(services)}", file=sys.stderr)
    raise SystemExit(1)

print("No duplicate published host ports found.")

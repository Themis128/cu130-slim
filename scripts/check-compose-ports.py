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

if duplicates:
    print("Duplicate published host ports detected:", file=sys.stderr)
    for port, services in sorted(duplicates.items()):
        print(f"  {port}: {', '.join(services)}", file=sys.stderr)
    raise SystemExit(1)

print("No duplicate published host ports found.")

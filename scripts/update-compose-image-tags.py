#!/usr/bin/env python3
"""Rewrite docker-compose.yml image pins for the single cu130-slim GHCR
package.
Usage: update-compose-image-tags.py <tag-suffix> [compose-file]
Example: update-compose-image-tags.py sha-8d1ef9a
  updates .../cu130-slim:social-api-latest -> .../cu130-slim:social-api-sha-8d1ef9a
  (tag-suffix is applied after each service name)"""

import re
import sys
from pathlib import Path

tag = sys.argv[1] if len(sys.argv) > 1 else ""
if not tag:
    print("Usage: update-compose-image-tags.py <tag-suffix> [compose-file]",
          file=sys.stderr)
    sys.exit(1)
compose_file = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("docker-compose.yml")

if not compose_file.is_file():
    print(f"error: compose file not found: {compose_file}", file=sys.stderr)
    sys.exit(1)

services = (
    "comfyui", "env-manager-backend", "env-manager-frontend",
    "social-api", "social-worker-publishing", "social-worker",
    "social-frontend", "cloudflared",
)
pattern = re.compile(
    r"(ghcr\.io/themis128/cu130-slim:)("
    + "|".join(re.escape(s) for s in sorted(services, key=len, reverse=True))
    + r")-[^\s\"']+"
)
text = compose_file.read_text(encoding="utf-8")
new_text, count = pattern.subn(rf"\g<1>\g<2>-{tag}", text)
if count == 0:
    print(f"error: no cu130-slim image refs updated in {compose_file}",
          file=sys.stderr)
    sys.exit(1)
compose_file.write_text(new_text, encoding="utf-8")
print(f"updated {count} image ref(s) in {compose_file} -> service-{tag}")

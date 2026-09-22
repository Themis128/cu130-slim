#!/usr/bin/env bash
# Rewrite docker-compose.yml image pins for the single cu130-slim GHCR package.
# Usage: scripts/update-compose-image-tags.sh <tag-suffix> [compose-file]
# Example: scripts/update-compose-image-tags.sh sha-8d1ef9a
#   updates .../cu130-slim:social-api-latest -> .../cu130-slim:social-api-sha-8d1ef9a
#   (tag-suffix is applied after each service name)
set -euo pipefail

TAG="${1:?Usage: $0 <tag-suffix> [compose-file]}"
COMPOSE_FILE="${2:-docker-compose.yml}"

if [[ ! -f "$COMPOSE_FILE" ]]; then
  echo "error: compose file not found: $COMPOSE_FILE" >&2
  exit 1
fi

export COMPOSE_TAG_UPDATE="$TAG"
export COMPOSE_FILE_UPDATE="$COMPOSE_FILE"

python3 -c "
import os, pathlib, re, sys
tag = os.environ['COMPOSE_TAG_UPDATE']
path = pathlib.Path(os.environ['COMPOSE_FILE_UPDATE'])
text = path.read_text(encoding='utf-8')
services = (
    'comfyui', 'env-manager-backend', 'env-manager-frontend',
    'social-api', 'social-worker-publishing', 'social-worker',
    'social-frontend', 'cloudflared',
)
pattern = re.compile(
    r'(ghcr\\.io/themis128/cu130-slim:)('
    + '|'.join(re.escape(s) for s in sorted(services, key=len, reverse=True))
    + r')-[^\\s\"\\']+'
)
new_text, count = pattern.subn(rf'\\1\\2-{tag}', text)
if count == 0:
    print(f'error: no cu130-slim image refs updated in {path}', file=sys.stderr)
    sys.exit(1)
path.write_text(new_text, encoding='utf-8')
print(f'updated {count} image ref(s) in {path} -> service-{tag}')
"

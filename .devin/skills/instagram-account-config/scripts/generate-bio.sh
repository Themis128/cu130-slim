#!/usr/bin/env bash
# Generate a stylish Instagram bio from LinkedIn profile data
# Usage: ./generate-bio.sh --name "Cloudless" --title "Founder @ " --skills "Cloud · Azure · AWS" --experience "15+ yrs" --location "Athens" --links "cloudless.gr" --style bold-brand
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"

docker compose exec -T social-api python /app/app/scripts/instagram_bio_generator.py "$@"

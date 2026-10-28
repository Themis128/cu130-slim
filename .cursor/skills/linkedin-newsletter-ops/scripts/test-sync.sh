#!/usr/bin/env bash
# Dry-run the subscriber sync — fetches page 1, prints stats, NO CRM writes.
set -eu
cd /home/tbaltzakis/cu130-slim
URN="${1:-$(grep -oP 'LINKEDIN_NEWSLETTER_SERIES_URN=\K.*' .env 2>/dev/null || true)}"
[ -n "$URN" ] || { echo "usage: test-sync.sh urn:li:fsd_contentSeries:<id>"; exit 1; }
docker compose exec -T social-api python - "$URN" <<'PYEOF'
import asyncio, sys
async def m():
    from app.services.linkedin_sidecar import LinkedInSidecarClient
    from app.services.newsletter_subscribers import fetch_subscribers
    c = LinkedInSidecarClient()
    cookies = await c.export_cookies()
    subs = await fetch_subscribers(cookies, sys.argv[1], max_pages=1)
    print(f"{len(subs)} subscribers on page 1:")
    for s in subs[:10]: print(" ", s["name"], "—", s["headline"][:60])
asyncio.run(m())
PYEOF

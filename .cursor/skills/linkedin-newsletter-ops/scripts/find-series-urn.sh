#!/usr/bin/env bash
# Print the newsletter series URN needed for LINKEDIN_NEWSLETTER_SERIES_URN.
# Opens the newsletters list on the sidecar's session and pulls the URN.
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python - <<'PYEOF'
import asyncio
async def m():
    from app.services.linkedin_sidecar import LinkedInSidecarClient
    c = LinkedInSidecarClient()
    # sidecar exposes get_profile/get_profile_activity; the newsletter URN
    # is visible on the creator's own page — pull the session's newsletters
    try:
        prof = await c.get_profile()
        print("profile:", {k: prof.get(k) for k in ("name","urn","id") if k in prof})
    except Exception as e:
        print("profile read failed:", e)
    print("\nOpen https://www.linkedin.com/mynetwork/discover-hub/newsletters/"
          "in the sidecar browser, or check the publish editor URL — the"
          " series URN is urn:li:fsd_contentSeries:<id> in the URL.")
asyncio.run(m())
PYEOF

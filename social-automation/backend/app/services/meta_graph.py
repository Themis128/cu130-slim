"""Meta Graph API versioning helpers.

SocialAuto integrates multiple Meta products that share the `graph.facebook.com`
host (Facebook Graph API family):
  - Facebook Pages
  - Messenger Platform (Send API, Messenger Profile, conversations, etc.)
  - Instagram Graph API (via graph.facebook.com)
  - WhatsApp Cloud API (built on Graph API; still uses graph.facebook.com)

Meta recommends explicitly versioning Graph API requests (avoid unversioned
requests which can default to an app-dashboard-selected or oldest-available
version). See:
  - https://developers.facebook.com/docs/graph-api/overview/
  - https://developers.facebook.com/docs/graph-api/guides/versioning/

Current available versions and schedules:
  - https://developers.facebook.com/docs/graph-api/changelog/versions/
"""

from __future__ import annotations

FACEBOOK_GRAPH_BASE = "https://graph.facebook.com"

# Latest Graph API version per Meta docs (updated 2026-09): v26.0
# https://developers.facebook.com/docs/graph-api/guides/versioning/
FACEBOOK_GRAPH_VERSION = "v26.0"


def facebook_graph_url(path: str, *, version: str = FACEBOOK_GRAPH_VERSION) -> str:
    """Build a versioned Graph API URL under graph.facebook.com."""
    clean = (path or "").lstrip("/")
    v = (version or "").lstrip("/")
    if not v:
        raise ValueError("Graph API version is empty")
    return f"{FACEBOOK_GRAPH_BASE}/{v}/{clean}"


"""UTM tagging for owned-domain links in published copy.

Convention (matches telegram_agent + lead-attribution on the site):

    utm_source   = publishing platform ("linkedin", "facebook", ...)
    utm_medium   = organic_social | channel_post | playbook-email ...
    utm_campaign = post source ("monetization-report", "visibility-era-day15")
                   or the fixed campaign name for agent-driven channels
    utm_content  = post.id / offer key — joins clicks back to a concrete post

Every site hit carries the tags through AttributionCapture -> the contact
form -> EspoCRM + web_analytics_events, closing the post -> lead loop.
Existing utm_* params are never overwritten (setdefault semantics).
"""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

OWNED_HOST_SUFFIX = "cloudless.gr"

# URLs end at whitespace/markup; keep trailing punctuation out of the match.
_URL_RE = re.compile(r"https?://[^\s<>'\"()\[\]]+")
_TRAILING_STRIP = ".,;:!?)]}"


def tracked_url(url: str, *, source: str, medium: str, campaign: str, content: str) -> str:
    """Tag an owned-domain URL with UTM attribution; pass through the rest."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if not parts.hostname or not parts.hostname.endswith(OWNED_HOST_SUFFIX):
        return url
    q = dict(parse_qsl(parts.query, keep_blank_values=True))
    q.setdefault("utm_source", source)
    q.setdefault("utm_medium", medium)
    q.setdefault("utm_campaign", campaign)
    q.setdefault("utm_content", content)
    return urlunsplit(parts._replace(query=urlencode(q)))


def tag_owned_links(text: str, *, platform: str, campaign: str, content: str) -> str:
    """Rewrite every cloudless.gr URL in ``text`` with platform-scoped UTMs.

    Non-owned URLs, mailto:, and bare domains are untouched. Idempotent —
    links already carrying utm_* (Telegram agent LTE links, author-set
    params) keep their values.
    """
    if not text or OWNED_HOST_SUFFIX not in text:
        return text

    def _tag(match: re.Match[str]) -> str:
        url = match.group(0)
        tail = ""
        while url and url[-1] in _TRAILING_STRIP:
            tail = url[-1] + tail
            url = url[:-1]
        return (
            tracked_url(
                url,
                source=platform,
                medium="organic_social",
                campaign=campaign,
                content=content,
            )
            + tail
        )

    return _URL_RE.sub(_tag, text)

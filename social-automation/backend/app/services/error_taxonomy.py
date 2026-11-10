"""Publish-error taxonomy — classify failures before deciding to retry.

Three buckets (industry pattern: classify first, retry second):

- ``transient``  — provider/network hiccup; retry with backoff
- ``terminal``   — deterministic failure; retrying cannot help, needs a
  human/content fix. Includes dead credentials: auth errors must NOT be
  retried (each retry draws fresh 401/429s and can trip platform
  invalid-request bans).
- ``unknown``    — neither list; bounded retries, then alert

``error_class`` is persisted on ``post_targets`` at failure time and
consumed by ``publish_retry`` — a stored field, not an inference at the
retry site, so legacy rows classify once and stay classified.
"""

from __future__ import annotations

TRANSIENT = "transient"
TERMINAL = "terminal"
UNKNOWN = "unknown"

# Ordered — first match wins. Keep patterns lowercase; matching is on
# ``error.lower()``.
_TERMINAL_PATTERNS: tuple[str, ...] = (
    # dead/expired credentials — the big retry-storm source
    "session has been invalidated",
    "error validating access token",
    "invalid_grant",
    "invalid_token",
    "invalid refresh token",
    "token has been revoked",
    "expiredtoken",
    "expired",
    "401",
    "403",
    "unauthorized",
    "forbidden",
    "permission denied",
    "does not have permission",
    "insufficient permission",
    "account not connected",
    "token decrypt failed",
    "reauth",
    # deterministic content/media rejections
    "only photo or video",
    "9004",
    "2207052",
    "media_type",
    "invalid parameter",
    "invalid media",
    "unsupported media",
    "media processing",
    "does not accept",
    "unsupported platform",
    "duplicate",
    "identical content",
    "quality gate",
    "seo score",
    "empty content_text",
    "semantic",
    "preflight",
    "url_ownership_unverified",
    "domain not verified",
)

_TRANSIENT_PATTERNS: tuple[str, ...] = (
    "429",
    "too many requests",
    "rate limit",
    "ratelimit",
    "timeout",
    "timed out",
    "temporarily",
    "try again",
    "please retry",
    "503",
    "502",
    "500",
    "internal error",
    "bad gateway",
    "service unavailable",
    "econn",
    "connection reset",
    "connection refused",
    "network",
    "in_progress",
    "still processing",
    "not ready",
    "container_status",
    "media_not_ready",
)


def classify_publish_error(error: str | None, platform: str | None = None) -> str:
    """Return ``transient`` | ``terminal`` | ``unknown`` for a publish error.

    ``platform`` is accepted for future per-platform rules; matching is
    currently platform-agnostic.
    """
    del platform  # reserved
    if not error:
        return UNKNOWN
    msg = error.lower()
    if any(p in msg for p in _TRANSIENT_PATTERNS):
        return TRANSIENT
    if any(p in msg for p in _TERMINAL_PATTERNS):
        return TERMINAL
    return UNKNOWN

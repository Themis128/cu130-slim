"""docker-compose env-forwarding contract for the LinkedIn scope-infra lanes.

Every ``LINKEDIN_*`` knob in the shared ``x-worker-env`` anchor must also
reach the ``social-api`` environment block — the API serves the OAuth/admin
endpoints and the workers run the beat tasks; a knob present in only one
side silently disables the feature on the other.
"""

from __future__ import annotations

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[4] / "docker-compose.yml"

# The scope-infra knobs added for community moderation, offline conversions,
# ads guard and feed engagement — each must be forwarded to BOTH the worker
# anchor and social-api.
REQUIRED_LINKEDIN_ENV = {
    "LINKEDIN_ADVANCED",
    "LINKEDIN_CONVERSION_RULE",
    "LINKEDIN_CONVERSION_RULE_LEAD_CREATED",
    "LINKEDIN_CONVERSION_INGEST_KEY",
    "LINKEDIN_LEAD_SYNC_KEY",
    "LINKEDIN_FEED_WRITES",
    "LINKEDIN_ENGAGE_AUTHORS",
    "LINKEDIN_ENGAGE_MAX_REACTIONS_DAY",
    "LINKEDIN_ENGAGE_MAX_COMMENTS_DAY",
    "LINKEDIN_ENGAGE_COMMENT_TEMPLATES",
    "LINKEDIN_ENGAGE_ORG_MAX_REACTIONS_DAY",
    "LINKEDIN_ENGAGE_MAX_AGE_H",
    "LINKEDIN_ENGAGE_POSTS_PER_AUTHOR",
    "LINKEDIN_CONVERSION_RULE_WON",
    "LINKEDIN_COMMENT_HIDE",
    "LINKEDIN_COMMUNITY_MAX_POSTS",
    "LINKEDIN_COMMUNITY_MAX_AGE_H",
    "LINKEDIN_COMMUNITY_ALERT_MIN_LEAD",
    "LINKEDIN_OFFLINE_CONVERSION_STATUSES",
    "LINKEDIN_OFFLINE_CONVERSION_MAX",
    "LINKEDIN_ADS_ALLOWED_ACTIVE",
}


def _region(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i + len(start))
    return text[i:j]


def _env_names(region: str) -> set[str]:
    return set(re.findall(r"^\s*-\s+([A-Z][A-Z0-9_]*)=", region, re.M))


def test_compose_file_exists():
    assert COMPOSE.is_file(), f"docker-compose.yml not found at {COMPOSE}"


def test_linkedin_scope_env_in_worker_anchor_and_api():
    text = COMPOSE.read_text(encoding="utf-8")
    worker = _region(text, "x-worker-env:", "x-worker-volumes:")
    api = _region(text, "  social-api:", "  social-metrics:")
    worker_vars = _env_names(worker) & REQUIRED_LINKEDIN_ENV
    api_vars = _env_names(api) & REQUIRED_LINKEDIN_ENV
    missing_worker = REQUIRED_LINKEDIN_ENV - worker_vars
    missing_api = REQUIRED_LINKEDIN_ENV - api_vars
    assert not missing_worker, f"LINKEDIN_* knobs missing from x-worker-env: {sorted(missing_worker)}"
    assert not missing_api, f"LINKEDIN_* knobs missing from social-api env: {sorted(missing_api)}"


def test_every_worker_linkedin_knob_reaches_api():
    """Guard against future drift: anything LINKEDIN_* in the anchor must
    also be in social-api (credentials like CLIENT_SECRET are api-only and
    intentionally excluded — they are not in the anchor either)."""
    text = COMPOSE.read_text(encoding="utf-8")
    worker = _region(text, "x-worker-env:", "x-worker-volumes:")
    api = _region(text, "  social-api:", "  social-metrics:")
    worker_li = {n for n in _env_names(worker) if n.startswith("LINKEDIN_")}
    api_li = {n for n in _env_names(api) if n.startswith("LINKEDIN_")}
    assert worker_li <= api_li, f"worker-only LINKEDIN knobs: {sorted(worker_li - api_li)}"

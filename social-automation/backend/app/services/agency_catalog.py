"""Service catalog + lead triage classifier for the autonomous agency.

The fixed-scope offer on cloudless.gr is four services priced €800–€2,400
each and a €3,600/month full bundle, all delivered inside a 14-day window.
This module is the deterministic core of the Front Desk agent: it maps a
lead's free-text description onto the catalog and extracts an auditable
domain. LLM polish may sit on top later — the routing decision stays
deterministic so it is testable and explainable (ABPM: guardrails over
free-form judgement).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Catalog — keep in sync with the public pricing on cloudless.gr.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Service:
    slug: str
    name: str
    price_low_eur: int
    price_high_eur: int
    summary: str


SERVICES: dict[str, Service] = {
    "cloud": Service(
        slug="cloud",
        name="Cloud Architecture & Migration",
        price_low_eur=1200,
        price_high_eur=2400,
        summary="Design and migrate workloads to Cloudflare, GCP, or Azure — "
        "cost down, resilience up.",
    ),
    "serverless": Service(
        slug="serverless",
        name="Serverless Development",
        price_low_eur=1000,
        price_high_eur=2000,
        summary="Event-driven applications that scale to zero when idle — "
        "Workers, D1, R2.",
    ),
    "analytics": Service(
        slug="analytics",
        name="Data Analytics & Dashboards",
        price_low_eur=800,
        price_high_eur=1600,
        summary="Dashboards, pipelines, and real-time reporting on your data.",
    ),
    "marketing": Service(
        slug="marketing",
        name="AI & Digital Marketing",
        price_low_eur=800,
        price_high_eur=1600,
        summary="AI-powered content strategy, SEO, and performance marketing.",
    ),
}

BUNDLE_NAME = "Full bundle (all four services)"
BUNDLE_PRICE_EUR = 3600  # per month — a fraction of one hire

DELIVERY_DAYS = 14
AUDIT_CTA_URL = "https://cloudless.gr/contact"

# ---------------------------------------------------------------------------
# Service-fit keyword model — en + el, weighted, explainable.
# ---------------------------------------------------------------------------

_SERVICE_SIGNALS: dict[str, tuple[tuple[str, int], ...]] = {
    "cloud": (
        ("migrat", 3), ("cloud", 3), ("aws", 2), ("azure", 2), ("gcp", 2),
        ("google cloud", 3), ("kubernetes", 2), ("k8s", 2), ("docker", 1),
        ("server", 1), ("hosting", 2), ("infrastructure", 2), ("devops", 2),
        ("cost", 1), ("bill", 1), ("scaling", 1), ("downtime", 1), ("uptime", 1),
        ("on-premise", 2), ("on premise", 2), ("datacenter", 2), ("data center", 2),
        ("μετανάστευση", 3), ("cloud", 3), ("υποδομή", 2), ("servers", 1),
        ("φιλοξενία", 2), ("κόστος", 1), ("διακοπές", 1),
    ),
    "serverless": (
        ("serverless", 4), ("workers", 2), ("lambda", 2), ("event-driven", 3),
        ("edge", 2), ("api", 1), ("backend", 1), ("microservice", 2),
        ("function", 1), ("queue", 1), ("scale to zero", 3), ("pay per use", 2),
        ("cloudflare workers", 4), ("d1", 1), ("r2", 1), ("kv", 1),
        ("χωρίς servers", 3), ("εφαρμογή", 1), ("ανάπτυξη", 1),
    ),
    "analytics": (
        ("analytic", 3), ("dashboard", 3), ("report", 2), ("metric", 2),
        ("kpi", 2), ("data", 1), ("pipeline", 2), ("warehouse", 2),
        ("bi", 1), ("business intelligence", 3), ("tracking", 1),
        ("measurement", 2), ("insight", 1), ("metabase", 2), ("grafana", 2),
        ("αναλυτικά", 3), ("αναφορές", 2), ("δεδομένα", 1), ("πίνακας", 2),
    ),
    "marketing": (
        ("marketing", 3), ("seo", 3), ("ads", 2), ("advertis", 2),
        ("campaign", 2), ("content", 1), ("social media", 2), ("leads", 2),
        ("growth", 2), ("brand", 1), ("landing page", 2), ("conversion", 2),
        ("newsletter", 1), ("email campaign", 2), ("google ads", 3),
        ("facebook ads", 3), ("linkedin", 1), ("audience", 1),
        ("μάρκετινγκ", 3), ("διαφήμιση", 3), ("πελάτες", 1), ("πωλήσεις", 1),
        ("περιεχόμενο", 1),
    ),
}

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.I)
_BARE_DOMAIN_RE = re.compile(
    r"\b(?:www\.)?([a-z0-9][a-z0-9-]{1,62}\.(?:com|gr|net|org|io|dev|app|co|eu|ai|me|info|biz|shop|store|site|online|tech|cloud|agency|digital|consulting|services|solutions|gov|edu)(?:\.[a-z]{2,3})?)\b",
    re.I,
)
_OWN_DOMAINS = {"cloudless.gr", "social.cloudless.gr", "example.com", "example.gr"}

# Never an auditable client site — free-mail hosts and social platforms
# (leads paste profile URLs, e.g. linkedin.com/in/name, as their "website").
_FREE_MAIL_AND_SOCIAL = {
    "gmail.com", "yahoo.com", "yahoo.gr", "hotmail.com", "outlook.com",
    "icloud.com", "proton.me", "protonmail.com", "aol.com", "gmx.com",
    "linkedin.com", "facebook.com", "fb.com", "instagram.com", "x.com",
    "twitter.com", "threads.net", "tiktok.com", "youtube.com", "youtu.be",
    "wa.me", "t.me", "telegram.me", "github.com", "linktr.ee", "calendly.com",
    "wikipedia.org", "maps.google.com", "google.com",
}


def classify_services(text: str) -> list[tuple[str, int, list[str]]]:
    """Score the free-text lead description against the catalog.

    Returns [(slug, score, matched_signals)] sorted by score desc; only
    services with score >= 2 are returned. Deterministic and explainable —
    the matched signal list is written into the triage note.
    """
    t = (text or "").lower()
    out: list[tuple[str, int, list[str]]] = []
    for slug, signals in _SERVICE_SIGNALS.items():
        score = 0
        matched: list[str] = []
        for needle, weight in signals:
            if needle in t:
                score += weight
                matched.append(needle)
        if score >= 2:
            out.append((slug, score, matched))
    out.sort(key=lambda x: -x[1])
    return out


def extract_domain(*texts: str | None) -> str | None:
    """First plausible client domain found in the lead's fields.

    Prefers an explicit URL, then a bare domain mention; skips our own
    domains and the common free-mail hosts an untrained lead might paste.
    """
    for text in texts:
        if not text:
            continue
        for m in _URL_RE.finditer(text):
            host = re.sub(r"^https?://", "", m.group(0), flags=re.I).split("/")[0].split(":")[0].lower()
            host = re.sub(r"^www\.", "", host)
            if host and host not in _OWN_DOMAINS and host not in _FREE_MAIL_AND_SOCIAL:
                return host
    for text in texts:
        if not text:
            continue
        for m in _BARE_DOMAIN_RE.finditer(text):
            host = m.group(1).lower()
            if host not in _OWN_DOMAINS and host not in _FREE_MAIL_AND_SOCIAL:
                return host
    return None


def price_range(services: list[str]) -> tuple[int, int]:
    """Bundle all four → flat bundle price; otherwise sum the ranges."""
    slugs = [s for s in services if s in SERVICES]
    if not slugs:
        return (0, 0)
    if len(slugs) == 4:
        return (BUNDLE_PRICE_EUR, BUNDLE_PRICE_EUR)
    return (
        sum(SERVICES[s].price_low_eur for s in slugs),
        sum(SERVICES[s].price_high_eur for s in slugs),
    )

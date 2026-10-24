"""The Architect — deterministic site audit for inbound leads.

Replaces the manual 30-minute audit call's first pass: fetch the lead's
public site, run fixed checks (transport security, response-header hygiene,
delivery performance, SEO fundamentals, stack fingerprint, analytics
presence), emit a 0-100 score, structured findings, and an action plan
whose items map onto the service catalog. Everything here is measured —
no fabricated metrics (GLOBAL_FRAME). LLM copy can sit on top later; the
evidence layer stays deterministic.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

from app.services.url_safety import validate_public_http_url

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT = 12.0
_MAX_HTML = 400_000
_UA = "Cloudless-SiteAudit/1.0 (+https://cloudless.gr)"

_SECURITY_HEADERS: tuple[tuple[str, str, str], ...] = (
    ("strict-transport-security", "hsts", "HTTPS strict transport (HSTS)"),
    ("content-security-policy", "csp", "Content Security Policy"),
    ("x-content-type-options", "xcto", "X-Content-Type-Options: nosniff"),
    ("referrer-policy", "referrer", "Referrer-Policy"),
)
_FRAME_HEADERS = ("x-frame-options", "content-security-policy")

_TECH_SIGNATURES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r'<meta[^>]+name=["\']generator["\'][^>]+content=["\']([^"\']+)', re.I), "generator:{0}"),
    (re.compile(r"wp-content|wp-includes|wp-json", re.I), "wordpress"),
    (re.compile(r"/_next/|__NEXT_DATA__", re.I), "nextjs"),
    (re.compile(r"__NUXT__|/_nuxt/", re.I), "nuxt"),
    (re.compile(r"cdn\.shopify\.com|Shopify\.theme", re.I), "shopify"),
    (re.compile(r"wixstatic\.com|X-Wix-", re.I), "wix"),
    (re.compile(r"squarespace", re.I), "squarespace"),
    (re.compile(r"react-root|data-reactroot|__REACT", re.I), "react-spa"),
    (re.compile(r"wp-rocket|litespeed", re.I), "wp-cache-plugin"),
)

_ANALYTICS_SIGNATURES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"googletagmanager\.com|gtag\(|GTM-", re.I), "google-tag-manager"),
    (re.compile(r"google-analytics\.com|gtag/js\?id=G-", re.I), "ga4"),
    (re.compile(r"plausible\.io|data-domain=", re.I), "plausible"),
    (re.compile(r"matomo|piwik", re.I), "matomo"),
    (re.compile(r"connect\.facebook\.net.*/fbevents", re.I), "meta-pixel"),
    (re.compile(r"hotjar|clarity\.ms", re.I), "heatmap"),
)


def _finding(check: str, severity: str, finding: str, detail: str = "", service: str | None = None) -> dict[str, Any]:
    return {"check": check, "severity": severity, "finding": finding, "detail": detail, "service": service}


async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response | None:
    try:
        return await client.get(url, timeout=_FETCH_TIMEOUT)
    except Exception:
        return None


def _detect_tech(html: str, headers: httpx.Headers) -> list[str]:
    found: list[str] = []
    for pattern, label in _TECH_SIGNATURES:
        m = pattern.search(html)
        if m:
            found.append(label.format(m.group(1).strip()[:60]) if "{0}" in label else label)
    powered = headers.get("x-powered-by")
    if powered:
        found.append(f"powered-by:{powered.strip()[:60]}")
    server = headers.get("server")
    if server:
        found.append(f"server:{server.strip()[:40]}")
    # de-dupe preserving order
    seen: set[str] = set()
    return [x for x in found if not (x in seen or seen.add(x))]


def _detect_analytics(html: str) -> list[str]:
    return [label for pattern, label in _ANALYTICS_SIGNATURES if pattern.search(html)]


def _seo_checks(html: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    title = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    title_text = (title.group(1).strip() if title else "")
    if not title_text:
        out.append(_finding("seo_title", "warning", "Missing <title>", service="marketing"))
    elif not (20 <= len(title_text) <= 65):
        out.append(_finding(
            "seo_title", "info",
            f"Title length {len(title_text)} chars (target 20-65)",
            detail=title_text[:120], service="marketing",
        ))
    desc = re.search(
        r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']*)', html, re.I
    ) or re.search(r'<meta[^>]+content=["\']([^"\']*)["\'][^>]+name=["\']description', html, re.I)
    if not desc or len(desc.group(1).strip()) < 50:
        out.append(_finding("seo_desc", "warning", "Missing/short meta description", service="marketing"))
    if not re.search(r'<meta[^>]+name=["\']viewport["\']', html, re.I):
        out.append(_finding("seo_viewport", "warning", "Missing responsive viewport meta", service="serverless"))
    if not re.search(r"<h1[>\s]", html, re.I):
        out.append(_finding("seo_h1", "info", "No <h1> on the homepage", service="marketing"))
    if not re.search(r'<html[^>]+lang=', html, re.I):
        out.append(_finding("seo_lang", "info", "<html> missing lang attribute", service="marketing"))
    if not re.search(r'rel=["\']canonical["\']', html, re.I):
        out.append(_finding("seo_canonical", "info", "No canonical link", service="marketing"))
    return out


def _grade(findings: list[dict[str, Any]]) -> int:
    score = 100
    for f in findings:
        score -= {"critical": 15, "warning": 6, "info": 2}.get(f["severity"], 0)
    return max(0, score)


def _action_plan(findings: list[dict[str, Any]], tech: list[str], analytics: list[str]) -> tuple[str, list[str]]:
    """Findings → ordered plan + recommended service slugs."""
    services: list[str] = []
    lines: list[str] = []
    sev_rank = {"critical": 0, "warning": 1, "info": 2}
    ordered = sorted(findings, key=lambda f: sev_rank.get(f["severity"], 3))
    for f in ordered:
        svc = f.get("service")
        if svc and svc not in services:
            services.append(svc)
        lines.append(f"- **{f['finding']}** — {f['detail'] or f['check']}")
    if not analytics:
        lines.append("- **No analytics detected** — wire measurement before spending on traffic")
        if "analytics" not in services:
            services.append("analytics")
    if not lines:
        lines.append("- Baseline is healthy — scope work to growth levers rather than fixes")
    tech_line = f"Detected stack: {', '.join(tech)}\n\n" if tech else ""
    return f"{tech_line}Findings & recommended actions:\n" + "\n".join(lines), services


async def run_site_audit(url: str) -> dict[str, Any]:
    """Audit one public URL. Returns {url, score, findings, tech, analytics,
    action_plan, recommended_services} or raises UnsafeUrlError/RuntimeError."""
    safe_url = validate_public_http_url(url)

    started = time.monotonic()
    async with httpx.AsyncClient(
        follow_redirects=False, headers={"User-Agent": _UA}, timeout=_FETCH_TIMEOUT
    ) as client:
        try:
            resp = await client.get(safe_url)
        except httpx.HTTPError as exc:
            raise RuntimeError(f"site unreachable: {exc.__class__.__name__}") from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)

        findings: list[dict[str, Any]] = []
        redirect_target: str | None = None
        if resp.is_redirect:
            redirect_target = resp.headers.get("location")
            # Follow once to audit the page that actually renders.
            if redirect_target:
                final = await _get(client, str(httpx.URL(safe_url).join(redirect_target)))
                if final is not None and final.status_code == 200:
                    resp = final

        if resp.status_code != 200:
            findings.append(_finding(
                "http_status", "critical",
                f"Homepage returns HTTP {resp.status_code}",
                "Visitors and crawlers hit an error page first", "cloud",
            ))
        if safe_url.startswith("http://"):
            findings.append(_finding(
                "https", "critical", "Site served over plain HTTP",
                "No TLS — traffic is interceptable and browsers warn users", "cloud",
            ))

        h = resp.headers
        for header, slug, label in _SECURITY_HEADERS:
            if header not in {k.lower() for k in h.keys()}:
                findings.append(_finding(
                    f"hdr_{slug}", "warning", f"Missing {label}",
                    "Standard hardening header absent on the homepage", "cloud",
                ))
        if not any(x in {k.lower() for k in h.keys()} for x in _FRAME_HEADERS):
            findings.append(_finding(
                "hdr_frame", "warning", "No clickjacking protection",
                "Neither X-Frame-Options nor CSP frame-ancestors set", "cloud",
            ))
        if "x-powered-by" in {k.lower() for k in h.keys()}:
            findings.append(_finding(
                "hdr_powered_by", "info", "X-Powered-By header exposed",
                h.get("x-powered-by", "")[:80], "cloud",
            ))

        if elapsed_ms > 2500:
            findings.append(_finding(
                "ttfb", "warning", f"Slow first response ({elapsed_ms} ms)",
                "TTFB above 2.5s — caching/edge or backend work needed", "cloud",
            ))
        elif elapsed_ms > 900:
            findings.append(_finding(
                "ttfb", "info", f"First response {elapsed_ms} ms",
                "TTFB above the ~0.9s good band", "cloud",
            ))

        enc = h.get("content-encoding", "")
        size = len(resp.content)
        if not enc and size > 100_000:
            findings.append(_finding(
                "compression", "warning", "Uncompressed HTML payload",
                f"{size // 1024} KB transferred without gzip/brotli", "cloud",
            ))

        ctype = h.get("content-type", "")
        html = resp.text[:_MAX_HTML] if "text/html" in ctype or "text" in ctype else ""

        tech = _detect_tech(html, h) if html else []
        analytics_found = _detect_analytics(html) if html else []

        if html:
            findings.extend(_seo_checks(html))
            scripts = len(re.findall(r"<script\b[^>]*src=", html, re.I))
            if scripts >= 15:
                findings.append(_finding(
                    "script_weight", "info", f"{scripts} external scripts on the homepage",
                    "Script weight slows first paint", "serverless",
                ))

        # robots.txt + sitemap.xml probes — cheap, high-signal SEO checks.
        base = f"{httpx.URL(safe_url).scheme}://{httpx.URL(safe_url).host}"
        robots, sitemap = await asyncio.gather(
            _get(client, f"{base}/robots.txt"),
            _get(client, f"{base}/sitemap.xml"),
        )
        if robots is None or robots.status_code != 200:
            findings.append(_finding("robots", "info", "No robots.txt", service="marketing"))
        if sitemap is None or sitemap.status_code != 200:
            findings.append(_finding("sitemap", "info", "No sitemap.xml", service="marketing"))

    action_plan, services = _action_plan(findings, tech, analytics_found)
    return {
        "url": safe_url,
        "final_url": str(resp.url),
        "http_status": resp.status_code,
        "redirected_to": redirect_target,
        "ttfb_ms": elapsed_ms,
        "score": _grade(findings),
        "findings": findings,
        "tech": tech,
        "analytics": analytics_found,
        "action_plan": action_plan,
        "recommended_services": services,
    }

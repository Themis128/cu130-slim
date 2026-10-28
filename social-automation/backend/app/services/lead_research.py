"""Lead-diligence loop — autonomous company research for the Sales department.

Adopted from the ``guy-hartstein/company-research-agent`` pattern (deep-diligence
agent writing structured notes into the CRM), implemented natively on our own
stack — free sources only, no paid enrichment key required:

- lead's own ``website`` field  → homepage <title> + meta description
- company/account name        → Wikipedia REST summary (free API)
- EspoCRM fields              → industry / title / source context already stored

For every eligible lead without a prior research note, the sweep composes a
diligence note and attaches it to the Lead via ``add_lead_note`` — the note IS
the dedupe marker (a second sweep finds "Lead research" already attached and
skips). ``dry_run=True`` returns the would-be notes without writing anything.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from typing import Any

import httpx

from app.services import espocrm_client as espo

logger = logging.getLogger(__name__)

RESEARCH_MARKER = "Lead research —"
_MAX_PER_RUN = 10
_FETCH_TIMEOUT = 10.0
_MAX_REDIRECTS = 3

_SELECT = [
    "id",
    "name",
    "emailAddress",
    "website",
    "accountName",
    "accountId",
    "industry",
    "title",
    "source",
    "status",
    "createdAt",
]


def _candidate_leads(leads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Eligible research subjects: real status, has company context or site."""
    out = []
    for lead in leads:
        if lead.get("status") in ("Dead", "Converted"):
            continue
        # nothing to research a bare email-only lead
        if not (lead.get("website") or lead.get("accountName") or lead.get("name")):
            continue
        out.append(lead)
    return out


async def _already_researched(lead_id: str) -> bool:
    notes = await espo.list_all(
        "Note",
        select=["id", "post"],
        where=[
            {"type": "equals", "attribute": "parentType", "value": "Lead"},
            {"type": "equals", "attribute": "parentId", "value": lead_id},
        ],
        max_items=50,
    )
    return any(RESEARCH_MARKER in (n.get("post") or "") for n in notes)


def _public_url(url: str) -> str | None:
    """Reject non-HTTP schemes and hosts that resolve to private/loopback IPs."""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    host = re.sub(r"^https?://", "", url, flags=re.I).split("/")[0].split(":")[0]
    if not host or "." not in host:
        return None
    try:
        ip = ipaddress.ip_address(host)
        ips = [ip]
    except ValueError:
        try:
            ips = [ipaddress.ip_address(a[4][0]) for a in socket.getaddrinfo(host, None)][:4]
        except OSError:
            return None
    if not ips or any(i.is_private or i.is_loopback or i.is_reserved for i in ips):
        return None
    return url


async def _fetch_site_summary(url: str, client: httpx.AsyncClient) -> str | None:
    safe = _public_url(url)
    if not safe:
        return None
    try:
        resp = await client.get(safe, follow_redirects=True, timeout=_FETCH_TIMEOUT)
        if resp.status_code != 200 or "text/html" not in resp.headers.get("content-type", ""):
            return None
        html = resp.text[:200_000]
        m = re.search(r"<title[^>]*>([^<]{3,200})</title>", html, re.I)
        title = m.group(1).strip() if m else ""
        m = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{10,400})', html, re.I) or re.search(
            r'<meta[^>]+content=["\']([^"\']{10,400})["\'][^>]+name=["\']description', html, re.I
        )
        desc = m.group(1).strip() if m else ""
        parts = [p for p in (title, desc) if p]
        return " — ".join(parts)[:400] or None
    except Exception:
        return None


async def _wikipedia_summary(name: str, client: httpx.AsyncClient) -> str | None:
    """Free Wikipedia REST summary — first hit for the company/person name."""
    try:
        search = await client.get(
            "https://en.wikipedia.org/w/rest.php/v1/search/page",
            params={"q": name, "limit": 1},
            timeout=_FETCH_TIMEOUT,
        )
        pages = (search.json().get("pages") or []) if search.status_code == 200 else []
        if not pages:
            return None
        excerpt = re.sub(r"<[^>]+>", "", pages[0].get("excerpt") or "").strip()
        title = pages[0].get("title", "")
        return f"{title}: {excerpt}"[:400] if excerpt else None
    except Exception:
        return None


def _compose_note(lead: dict[str, Any], site: str | None, wiki: str | None) -> str | None:
    bits = []
    company = lead.get("accountName") or lead.get("name") or ""
    ctx = ", ".join(
        x
        for x in (
            f"title={lead['title']}" if lead.get("title") else "",
            f"industry={lead['industry']}" if lead.get("industry") else "",
            f"source={lead['source']}" if lead.get("source") else "",
        )
        if x
    )
    if ctx:
        bits.append(f"CRM context: {ctx}")
    if site:
        bits.append(f"Website ({lead.get('website')}): {site}")
    if wiki:
        bits.append(f"Wikipedia: {wiki}")
    if not bits:
        return None
    return f"{RESEARCH_MARKER} {company}\n" + "\n".join(bits)


async def run_lead_research(*, dry_run: bool = False, limit: int = _MAX_PER_RUN) -> dict[str, Any]:
    """Research every eligible lead that has no research note yet."""
    if not espo.configured():
        return {"status": "skipped", "reason": "espocrm_not_configured"}

    leads = _candidate_leads(await espo.list_all("Lead", select=_SELECT))

    researched, skipped, errors, notes = 0, 0, 0, []
    async with httpx.AsyncClient(
        headers={"User-Agent": "Cloudless-SocialAuto lead-research/1.0"},
    ) as client:
        for lead in leads[: max(limit, 1)]:
            lid = lead.get("id", "")
            try:
                if await _already_researched(lid):
                    skipped += 1
                    continue
                site_task = _fetch_site_summary(lead["website"], client) if lead.get("website") else None
                wiki_task = _wikipedia_summary(lead.get("accountName") or lead.get("name") or "", client)
                site = await site_task if site_task else None
                wiki = await wiki_task
                note = _compose_note(lead, site, wiki)
                if not note:
                    skipped += 1
                    continue
                if not dry_run:
                    ok = await espo.add_lead_note(lid, note, entity="Lead")
                    if not ok:
                        errors += 1
                        continue
                researched += 1
                notes.append({"lead_id": lid, "name": lead.get("name"), "note": note})
            except Exception as exc:
                errors += 1
                logger.warning("lead research failed for %s: %r", lid, exc)
            await asyncio.sleep(0)  # yield between leads

    stats = {
        "status": "ok",
        "dry_run": dry_run,
        "candidates": len(leads),
        "researched": researched,
        "skipped": skipped,
        "errors": errors,
        "notes": notes,
    }
    if researched or errors:
        await _slack_report(stats)
    return stats


async def _slack_report(stats: dict[str, Any]) -> None:
    try:
        from app.services.slack_notifications import post_digest_text_to_slack

        lines = [
            f"*Lead research sweep* — {stats['researched']} researched, "
            f"{stats['skipped']} skipped, {stats['errors']} errors "
            f"(of {stats['candidates']} candidates)",
        ]
        for n in stats["notes"][:5]:
            lines.append(f"• {n['name']}: {n['note'].splitlines()[0][:120]}")
        await post_digest_text_to_slack("\n".join(lines))
    except Exception:
        logger.exception("lead research: slack report failed")

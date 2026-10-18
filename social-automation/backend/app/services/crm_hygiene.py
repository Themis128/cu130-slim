"""Data-driven CRM hygiene — the closed-loop discipline layer for EspoCRM.

Adopted from the data-driven CRM playbook (capture → clean → score →
segment → route → lifecycle → closed-loop analytics). EspoCRM's paid
Workflows pack isn't installed, so the hygiene loop runs here on the
worker instead of inside the CRM:

- **Dedupe** — EspoCRM has no unique-email constraint; ``createLead``
  dedupes at ingress but webhooks/tests still slip dupes through.
- **Test quarantine** — CI/deploy probes (`ci-test+*@example.invalid`)
  must not pollute segments, counts, or campaign audiences.
- **Follow-up SLA** — New leads older than ``STALE_DAYS`` are surfaced to
  Slack; speed-to-lead is the single biggest conversion lever.
- **Closed-loop report** — weekly stats (by status/source, deduped,
  quarantined, stale) land in the log + Slack so the funnel is measured,
  not assumed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from app.services import espocrm_client

logger = logging.getLogger(__name__)

STALE_DAYS = 7
_TEST_MARKERS = ("ci-test+", "@example.invalid", "@example.com", "test@test")


def crm_hygiene_enabled() -> bool:
    return espocrm_client.configured()


def _norm_email(lead: dict[str, Any]) -> str:
    return str(lead.get("emailAddress") or "").strip().lower()


def _is_test_lead(lead: dict[str, Any]) -> bool:
    email = _norm_email(lead)
    name = str(lead.get("name") or "").lower()
    return any(m in email for m in _TEST_MARKERS) or "ci functional test" in name


def _age_days(lead: dict[str, Any], now: datetime) -> float | None:
    raw = lead.get("createdAt")
    if not raw:
        return None
    try:
        created = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return (now - created).total_seconds() / 86400


async def _mark_dead(lead: dict[str, Any], reason: str) -> bool:
    try:
        await espocrm_client.espo_patch(
            f"/Lead/{lead['id']}", {"status": "Dead"}
        )
        await espocrm_client.add_lead_note(
            lead["id"], f"[crm-hygiene] marked Dead: {reason}"
        )
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("crm hygiene: dead-mark failed for %s: %s", lead.get("id"), exc)
        return False


async def run_crm_hygiene(*, alert: bool = True) -> dict[str, Any]:
    """Weekly sweep. Non-destructive: nothing is deleted, only status=Dead
    + an audit note, so every action is reversible in the EspoCRM UI."""
    if not crm_hygiene_enabled():
        return {"enabled": False}

    leads = await espocrm_client.list_all(
        "Lead",
        select=["id", "name", "emailAddress", "status", "source", "createdAt"],
    )
    now = datetime.now(UTC)
    stats: dict[str, Any] = {
        "total": len(leads),
        "quarantined": 0,
        "deduped": 0,
        "errors": 0,
        "by_status": {},
        "by_source": {},
        "stale_new": [],
    }

    active: dict[str, list[dict[str, Any]]] = {}
    for lead in leads:
        status = str(lead.get("status") or "")
        source = str(lead.get("source") or "(none)")
        stats["by_status"][status] = stats["by_status"].get(status, 0) + 1
        stats["by_source"][source] = stats["by_source"].get(source, 0) + 1
        if status in ("Dead", "Converted"):
            continue
        if _is_test_lead(lead):
            if await _mark_dead(lead, "test/CI probe — quarantined"):
                stats["quarantined"] += 1
            else:
                stats["errors"] += 1
            continue
        email = _norm_email(lead)
        if email:
            active.setdefault(email, []).append(lead)

    # Dedupe: newest lead wins; older duplicates go Dead with a pointer.
    for email, dupes in active.items():
        if len(dupes) < 2:
            continue
        dupes.sort(key=lambda ld: str(ld.get("createdAt") or ""), reverse=True)
        keeper = dupes[0]
        for dup in dupes[1:]:
            if await _mark_dead(
                dup, f"duplicate of {keeper['id']} ({email}) — merged note"
            ):
                stats["deduped"] += 1
            else:
                stats["errors"] += 1

    # Follow-up SLA: live New leads past the window get named in the report.
    for dupes in active.values():
        for lead in dupes:
            if str(lead.get("status") or "") != "New":
                continue
            age = _age_days(lead, now)
            if age is not None and age > STALE_DAYS:
                stats["stale_new"].append(
                    {
                        "id": lead["id"],
                        "email": _norm_email(lead),
                        "source": lead.get("source"),
                        "age_days": round(age, 1),
                    }
                )
    stats["stale_new"].sort(key=lambda s: s["age_days"], reverse=True)
    stats["enabled"] = True

    logger.info("crm hygiene: %s", {k: v for k, v in stats.items() if k != "stale_new"})
    if alert:
        await _slack_report(stats)
    return stats


async def _slack_report(stats: dict[str, Any]) -> None:
    try:
        from app.services.slack_notifications import post_digest_text_to_slack

        lines = [
            "*CRM hygiene — weekly sweep*",
            f"Leads: {stats['total']} · quarantined {stats['quarantined']}"
            f" · deduped {stats['deduped']} · errors {stats['errors']}",
            "By status: "
            + ", ".join(f"{k}={v}" for k, v in sorted(stats["by_status"].items())),
            "By source: "
            + ", ".join(f"{k}={v}" for k, v in sorted(stats["by_source"].items())),
        ]
        if stats["stale_new"]:
            lines.append(f":warning: {len(stats['stale_new'])} New leads past {STALE_DAYS}d SLA:")
            for s in stats["stale_new"][:10]:
                lines.append(f"  • {s['email']} ({s['source']}, {s['age_days']}d old)")
        await post_digest_text_to_slack("\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.exception("crm hygiene: slack report failed")

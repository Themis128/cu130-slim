"""CRM engagement write-back — replies return to the CRM record.

The email drip and outbound mail write *send* notes onto leads, but inbound
replies never flowed back. This sweep reads the dovecot mailbox
(``SMTP_*``/``PROMO_SCAN_IMAP_*`` identity — same IMAP path as
``promo_scan``), matches sender addresses against live Lead/Contact emails,
and per new reply:

1. attaches a ``Reply received: <subject>`` audit note to the CRM record
   (the note marker dedupes across sweeps via the Message-ID stored in it),
2. bumps the record to ``In Process`` when it still sits at ``New``/``Assigned``
   — a reply is the strongest intent signal a lead can send,
3. Slack-alerts so a human sees the hot reply fast.

Read-only IMAP — nothing is flagged, moved, or deleted in the mailbox.
"""

from __future__ import annotations

import email.header
import email.utils
import imaplib
import logging
import re
import ssl
from datetime import UTC, datetime, timedelta
from typing import Any

from app.core.config import get_settings
from app.services import espocrm_client as espo

logger = logging.getLogger(__name__)

REPLY_MARKER = "Reply received"
REPLIES_TARGET_LIST = "Social engagement (replies)"
_STATUSES_ACTIVE = {"New", "Assigned"}
_SELECT = ["id", "name", "emailAddress", "status"]


def _decode(raw: str) -> str:
    """RFC2047 header decode — mirrors promo_scan's approach."""
    try:
        parts = email.header.decode_header(raw)
        return "".join(p.decode(enc or "utf-8", "replace") if isinstance(p, bytes) else str(p) for p, enc in parts)
    except Exception:  # noqa: BLE001
        return raw


def _from_address(header: str) -> str:
    _, addr = email.utils.parseaddr(header)
    return (addr or "").strip().lower()


def _fetch_candidates(lookback_days: int) -> tuple[list[dict[str, Any]], list[str]]:
    """Header-only IMAP scan — returns {from, subject, message_id, uid}."""
    settings = get_settings()
    host = settings.PROMO_SCAN_IMAP_HOST or settings.SMTP_HOST
    user = settings.PROMO_SCAN_MAILBOX or settings.SMTP_USER
    password = settings.PROMO_SCAN_IMAP_PASS or settings.SMTP_PASSWORD
    port = settings.PROMO_SCAN_IMAP_PORT
    if not (host and user and password):
        return [], ["IMAP credentials not configured"]

    out: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        ctx = ssl.create_default_context() if settings.SMTP_SSL_VERIFY else ssl._create_unverified_context()  # noqa: SLF001
        since = (datetime.now(UTC) - timedelta(days=lookback_days)).strftime("%d-%b-%Y")
        with imaplib.IMAP4_SSL(host, port, ssl_context=ctx) as mail:
            mail.login(user, password)
            mail.select("INBOX", readonly=True)
            status, data = mail.search(None, "SINCE", since)
            if status != "OK" or not data or not data[0]:
                return [], []
            for msg_id in data[0].split()[-300:]:
                status, data = mail.fetch(
                    msg_id,
                    "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT MESSAGE-ID IN-REPLY-TO)])",
                )
                if status != "OK" or not data or not isinstance(data[0], tuple):
                    continue
                header = data[0][1].decode("utf-8", "replace")
                frm = re.search(r"(?im)^From:\s*(.+)$", header)
                subj = re.search(r"(?im)^Subject:\s*(.+)$", header)
                mid = re.search(r"(?im)^Message-ID:\s*(.+)$", header)
                irt = re.search(r"(?im)^In-Reply-To:\s*(.+)$", header)
                out.append(
                    {
                        "uid": msg_id.decode() if isinstance(msg_id, bytes) else str(msg_id),
                        "from": _from_address(_decode(frm.group(1) if frm else "")),
                        "subject": _decode(subj.group(1).strip() if subj else "(no subject)"),
                        "message_id": (mid.group(1).strip() if mid else ""),
                        "in_reply_to": bool(irt),
                    }
                )
    except Exception as exc:  # noqa: BLE001 — report still goes out
        logger.exception("CRM engagement: mailbox scan failed")
        errors.append(f"mailbox scan: {type(exc).__name__}: {str(exc)[:160]}")
    return out, errors


async def _crm_emails() -> dict[str, dict[str, Any]]:
    """email → live CRM record (Lead wins; Contacts also tracked)."""
    profiles: dict[str, dict[str, Any]] = {}
    for entity in ("Lead", "Contact"):
        for rec in await espo.list_all(entity, select=_SELECT):
            email = str(rec.get("emailAddress") or "").strip().lower()
            if not email or rec.get("status") in ("Dead", "Converted"):
                continue
            profiles.setdefault(email, {**rec, "entity": entity})
    return profiles


async def _already_noted(rec: dict[str, Any], marker: str) -> bool:
    notes = await espo.list_all(
        "Note",
        select=["id", "post"],
        where=[
            {"type": "equals", "attribute": "parentType", "value": rec["entity"]},
            {"type": "equals", "attribute": "parentId", "value": rec["id"]},
        ],
        max_items=100,
    )
    return any(marker in (n.get("post") or "") for n in notes)


async def _link_replies_list(lead_id: str, list_id: str) -> bool:
    """Union the replies TargetList into a Lead's memberships.

    ``targetListsIds`` is set-replacing and is not returned by ``select``, so
    current memberships must come from the relation endpoint. A failed read
    means "don't touch" — never write ``[]`` and wipe existing lists."""
    try:
        rel = await espo.espo_get(f"/Lead/{lead_id}/targetLists", params={"select": "id"})
    except Exception:  # noqa: BLE001
        logger.exception("crm engagement: targetLists read failed for lead %s", lead_id)
        return False
    current = [m["id"] for m in (rel.get("list") or []) if m.get("id")]
    if list_id in current:
        return True
    await espo.espo_patch(f"/Lead/{lead_id}", {"targetListsIds": [*current, list_id]})
    return True


async def run_crm_engagement_sweep(*, dry_run: bool = False) -> dict[str, Any]:
    """Match inbound replies to CRM records; write engagement notes."""
    settings = get_settings()
    if not getattr(settings, "CRM_ENGAGEMENT_ENABLED", True):
        return {"enabled": False}
    lookback = int(getattr(settings, "CRM_ENGAGEMENT_LOOKBACK_DAYS", 7) or 7)
    candidates, errors = _fetch_candidates(lookback)
    profiles = await _crm_emails()

    stats: dict[str, Any] = {
        "scanned": len(candidates),
        "matched": 0,
        "noted": 0,
        "status_bumped": 0,
        "list_enrolled": 0,
        "errors": len(errors),
        "dry_run": dry_run,
        "details": [],
    }
    own_domain = settings.SMTP_FROM.rsplit("@", 1)[-1].strip().lower()
    replies_list_id = await espo.target_list_id(REPLIES_TARGET_LIST)
    if replies_list_id is None:
        logger.warning("crm engagement: TargetList %r not found", REPLIES_TARGET_LIST)

    for cand in candidates:
        sender = cand["from"]
        if not sender or sender.endswith(f"@{own_domain}"):
            continue  # our own sends/drips — not inbound engagement
        rec = profiles.get(sender)
        if not rec:
            continue
        stats["matched"] += 1
        marker = f"{REPLY_MARKER}: {cand['subject'][:120]} · msg {cand['message_id'] or cand['uid']}"
        try:
            if await _already_noted(rec, cand["message_id"] or f"uid {cand['uid']}"):
                continue
            entry = {"email": sender, "subject": cand["subject"][:80], "entity": rec["entity"]}
            if not dry_run:
                await espo.add_lead_note(rec["id"], marker, entity=rec["entity"])
                stats["noted"] += 1
                if rec["entity"] == "Lead":
                    if replies_list_id and await _link_replies_list(rec["id"], replies_list_id):
                        stats["list_enrolled"] += 1
                    if rec.get("status") in _STATUSES_ACTIVE:
                        await espo.espo_patch(f"/Lead/{rec['id']}", {"status": "In Process"})
                        stats["status_bumped"] += 1
                        entry["status"] = "In Process"
            stats["details"].append(entry)
        except Exception:  # noqa: BLE001 — one bad record must not stall the sweep
            logger.exception("crm engagement: record %s failed", rec["id"])
            stats["errors"] += 1

    errors and stats.setdefault("scan_errors", errors)
    await _slack_report(stats)
    return stats


async def _slack_report(stats: dict[str, Any]) -> None:
    if not stats["details"] and not stats["errors"]:
        return  # silence is correct — no replies, no noise
    try:
        from app.services.slack_notifications import post_digest_text_to_slack
    except Exception:  # noqa: BLE001
        return
    lines = [
        "*CRM engagement sweep* — "
        f"{stats['matched']} reply-matches, {stats['noted']} noted, "
        f"{stats['status_bumped']} leads → In Process, {stats['errors']} errors" + (" (dry run)" if stats["dry_run"] else "")
    ]
    for d in stats["details"][:5]:
        lines.append(f"• {d['email']}: {d['subject']}")
    try:
        await post_digest_text_to_slack("\n".join(lines))
    except Exception:  # noqa: BLE001
        logger.exception("crm engagement: slack report failed")

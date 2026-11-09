"""Daily Slack ops journal → Nextcloud Notes + company brain.

Scans the SocialAuto workspace's alert/ops channels for the last 24h,
classifies what needs attention, and writes a markdown digest to a
Nextcloud Note (``Slack Ops — YYYY-MM-DD``, category ``Ops``) so the
workspace always carries the latest operational state — same standing
directive as the docs mirror. A one-line summary is also journaled into
each team's company brain so ``recall()`` surfaces it to the strategist.

Reads via ``conversations.history`` on ``SLACK_BOT_TOKEN`` (the synced
mcp-remote user token carries channels:history/groups:history). Only
channels in the SocialAuto workspace resolve — www.cloudless.gr channels
need that workspace's token and are out of scope here.

Runs on a daily beat (``slack-ops-journal``); safe to trigger manually.
"""

from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import httpx

from app.core.config import settings
from app.services import company_brain

logger = logging.getLogger(__name__)

# SocialAuto workspace (T0C18DXH8P7) channels worth journaling. IDs are
# stable; names are for the report header. Override/extend via
# SLACK_OPS_CHANNELS="name:C…;name:C…" if channels are added.
_DEFAULT_CHANNELS = {
    "alerts": "C0C1D46212N",  # #socialauto-alerts
    "engagement-gate": "C0CBVCZ0W4V",
    "publishing": "C0C1B8PMTUN",  # #socialauto-publishing
    "approvals": "C0C1B8WM8BG",
    "leads": "C0C3XKEHPM1",  # #socialauto-leads
    "socialauto": "C0C1F1K3DDF",  # #socialauto digests/ops
}

_WINDOW_S = 24 * 3600
_MAX_PER_CHANNEL = 15
_NOTE_CATEGORY = "Ops"

# severity markers scanned in message text + attachment fallback
_INCIDENT_MARKERS = (
    "rotating_light",
    "restart cap",
    "needs a human",
    "workflow failed",
    "n8n workflow failed",
    "dead",
    "invalid_auth",
    "channel_not_found",
)
_WARNING_MARKERS = (
    "warning",
    "needs you",
    "needs a verification",
    "blocked",
    "exited",
    "timed out",
    "only 3/4",
)
# noise we don't surface as issues
_NOISE_MARKERS = ("published", "new lead captured", "has joined", "recovered")


def slack_ops_journal_enabled() -> bool:
    return bool((settings.SLACK_BOT_TOKEN or "").strip())


def _channels() -> dict[str, str]:
    raw = (os.environ.get("SLACK_OPS_CHANNELS") or "").strip()
    if not raw:
        return dict(_DEFAULT_CHANNELS)
    out: dict[str, str] = {}
    for part in raw.split(";"):
        if ":" in part:
            name, cid = part.split(":", 1)
            out[name.strip()] = cid.strip()
    return out or dict(_DEFAULT_CHANNELS)


def _slack_api(method: str, **params) -> dict:
    token = (settings.SLACK_BOT_TOKEN or "").strip()
    params["token"] = token
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=urllib.parse.urlencode(params).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read())
    except Exception as exc:  # noqa: BLE001
        logger.warning("slack ops journal: %s failed: %s", method, exc)
        return {}


def _message_text(msg: dict) -> str:
    text = (msg.get("text") or "").strip()
    if text:
        return text
    # bot/alert posts often carry content only in attachments
    parts = []
    for att in msg.get("attachments") or []:
        for key in ("fallback", "pretext", "title", "text"):
            v = att.get(key)
            if isinstance(v, str) and v.strip():
                parts.append(v.strip())
    return " | ".join(parts)


def classify(text: str) -> str:
    """incident | warning | info — pure, unit-tested."""
    low = text.lower()
    if any(m in low for m in _INCIDENT_MARKERS):
        return "incident"
    if any(m in low for m in _WARNING_MARKERS):
        return "warning"
    return "info"


def _is_noise(text: str) -> bool:
    low = text.lower()
    return any(m in low for m in _NOISE_MARKERS)


def collect_window() -> dict[str, list[dict]]:
    """Fetch last-24h messages per channel → {channel_name: [msgs]}.

    Synchronous Slack API wrapped for the async caller; failures degrade
    to an empty list so one dead channel never sinks the journal.
    """
    oldest = f"{time.time() - _WINDOW_S:.6f}"
    out: dict[str, list[dict]] = {}
    for name, cid in _channels().items():
        resp = _slack_api("conversations.history", channel=cid, oldest=oldest, limit=200)
        msgs = resp.get("messages") or []
        if not resp.get("ok"):
            logger.info("slack ops journal: %s history failed: %s", name, resp.get("error"))
        out[name] = msgs[:_MAX_PER_CHANNEL]
    return out


def build_report(by_channel: dict[str, list[dict]], day: str) -> tuple[str, dict]:
    """Render the markdown digest. Returns (markdown, stats)."""
    stats = {"incident": 0, "warning": 0, "info": 0, "channels": 0, "messages": 0}
    attention: list[str] = []
    sections: list[str] = []

    for name, msgs in by_channel.items():
        if not msgs:
            continue
        stats["channels"] += 1
        stats["messages"] += len(msgs)
        lines = [f"### #{name} — {len(msgs)} msgs/24h"]
        for m in msgs:
            text = _message_text(m)
            if not text:
                continue
            sev = classify(text)
            stats[sev] += 1
            ts = datetime.fromtimestamp(float(m["ts"]), UTC).strftime("%H:%M")
            snippet = text.replace("\n", " ")[:220]
            if sev == "incident":
                lines.append(f"- :rotating_light: `{ts}` {snippet}")
                attention.append(f"#{name}: {snippet[:160]}")
            elif sev == "warning":
                lines.append(f"- :warning: `{ts}` {snippet}")
                attention.append(f"#{name}: {snippet[:160]}")
            elif not _is_noise(text):
                lines.append(f"- `{ts}` {snippet}")
        sections.append("\n".join(lines))

    md = [
        f"# Slack Ops Journal — {day}",
        "",
        f"**24h window** · {stats['messages']} messages across {stats['channels']} channels",
        f"**Needs attention:** {stats['incident']} incidents · {stats['warning']} warnings",
        "",
    ]
    if attention:
        md += ["## Needs attention", ""] + [f"- {a}" for a in attention[:25]] + [""]
    md += sections
    md += [
        "",
        "_Generated by `slack-ops-journal` (SLACK_BOT_TOKEN → conversations.history → Nextcloud Notes)._",
    ]
    return "\n".join(md), stats


async def _write_note(title: str, content: str) -> bool:
    """Create-or-update a Nextcloud Note via the Notes API (category Ops)."""
    base = (settings.NEXTCLOUD_BASE_URL or "").strip().rstrip("/")
    if not base and "/remote.php" in (settings.NEXTCLOUD_DAV_URL or ""):
        base = settings.NEXTCLOUD_DAV_URL.split("/remote.php")[0].rstrip("/")
    if not base or not settings.NEXTCLOUD_USERNAME.strip():
        return False
    auth = (settings.NEXTCLOUD_USERNAME, settings.NEXTCLOUD_APP_PASSWORD)
    api = f"{base}/index.php/apps/notes/api/v1/notes"
    async with httpx.AsyncClient(auth=auth, timeout=20.0) as client:
        try:
            note_id = None
            r = await client.get(api)
            if r.status_code == 200:
                for note in r.json():
                    if note.get("title") == title:
                        note_id = note["id"]
                        break
            if note_id is not None:
                r = await client.put(
                    f"{api}/{note_id}",
                    json={"content": content, "category": _NOTE_CATEGORY},
                )
            else:
                r = await client.post(
                    api,
                    json={"title": title, "content": content, "category": _NOTE_CATEGORY},
                )
            r.raise_for_status()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning("slack ops journal note write failed: %s", exc)
            return False


async def run_journal() -> dict:
    """Collect → classify → Nextcloud note → company-brain memory line."""
    day = datetime.now(UTC).date().isoformat()
    by_channel = collect_window()
    md, stats = build_report(by_channel, day)

    title = f"Slack Ops — {day}"
    note_ok = await _write_note(title, md)

    memory_ok = False
    summary = f"Slack ops {day}: {stats['messages']} msgs/24h, {stats['incident']} incidents, {stats['warning']} warnings across {stats['channels']} channels."
    for team_id in await _team_ids():
        memory_ok = (
            await company_brain.remember(
                team_id,
                f"slack-ops-{day}",
                summary + "\n\n" + md[:4000],
                metadata={"kind": "slack-ops-journal", "day": day, **stats},
            )
            or memory_ok
        )

    logger.info("slack ops journal: %s", {"note": note_ok, "memory": memory_ok, **stats})
    return {"day": day, "note": note_ok, "memory": memory_ok, **stats}


async def _team_ids() -> list:
    """All team ids — the Slack workspace is shared, journal once per team."""
    from sqlalchemy import select

    from app.db.session import async_session_maker
    from app.models.user import Team

    try:
        async with async_session_maker() as db:
            return list((await db.execute(select(Team.id))).scalars().all())
    except Exception as exc:  # noqa: BLE001
        logger.warning("slack ops journal: team lookup failed: %s", exc)
        return []

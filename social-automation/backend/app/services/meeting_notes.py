"""Consultation-call → Talk room → recording → transcript → EspoCRM notes.

Two sweeps (one Celery task, ``talk-meeting-notes``, every 30 min):

- ``ensure_consult_rooms`` — every EspoCRM Opportunity parked at
  ``appointmentsscheduled`` with a future ``closeDate`` gets a dedicated
  public Talk room (``Consultation — <name> — <date>``), a welcome message,
  and a CRM note carrying the ``talk_room:<token>`` marker + join link.
  Guests join via ``https://cloud.cloudless.gr/call/<token>`` — the same
  public-room pattern as the existing "Cloudless Consultations" room.
- ``process_meeting_notes`` — opportunities whose meeting day has passed
  get their Talk room scanned for a shared recording file; the recording
  is downloaded, transcribed (Cloudflare Workers AI Whisper), summarised
  into minutes by DMR, and attached to the Opportunity as a
  ``Meeting notes:``-marked note (the marker is the dedupe key).

Live transcription is NOT used: spreed 25.x exposes it only behind an
ExApp that isn't installed (live-transcription=false in capabilities), so
post-call transcription of the moderator-started recording is the honest
path — it also produces a durable artifact.

Nothing here raises into callers; every stage degrades to a stat.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import tempfile
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import espocrm_client as espo
from app.services import nextcloud_talk as talk

logger = logging.getLogger(__name__)

CONSULT_STAGE = "appointmentscheduled"
ROOM_TAG = "talk_room:"
NOTES_TAG = "Meeting notes:"
ROOM_TAG_RE = re.compile(r"talk_room:([a-z0-9]{8})")

_MAX_RECORDING_BYTES = 300 * 1024 * 1024  # 300 MB — longer calls are skipped


def _factory(custom=None):
    if custom is not None:
        return custom
    from app.worker._async import get_session_factory

    return get_session_factory()


def _enabled() -> bool:
    return talk.talk_enabled() and espo.configured()


async def _opps_at_consult() -> list[dict[str, Any]]:
    return await espo.list_all(
        "Opportunity",
        select=["id", "name", "stage", "closeDate", "accountId", "accountName"],
        where=[{"type": "equals", "attribute": "stage", "value": CONSULT_STAGE}],
        max_items=100,
    )


async def _opp_has_marker(opp_id: str, marker: str) -> bool:
    notes = await espo.list_all(
        "Note",
        select=["id", "post"],
        where=[
            {"type": "equals", "attribute": "parentType", "value": "Opportunity"},
            {"type": "equals", "attribute": "parentId", "value": opp_id},
        ],
        max_items=50,
    )
    return any(marker in (n.get("post") or "") for n in notes)


async def _opp_notes(opp_id: str) -> list[dict[str, Any]]:
    return await espo.list_all(
        "Note",
        select=["id", "post"],
        where=[
            {"type": "equals", "attribute": "parentType", "value": "Opportunity"},
            {"type": "equals", "attribute": "parentId", "value": opp_id},
        ],
        max_items=50,
    )


def _token_from_notes(notes: list[dict[str, Any]]) -> str | None:
    for n in notes:
        m = ROOM_TAG_RE.search(n.get("post") or "")
        if m:
            return m.group(1)
    return None


async def _lead_id_for_email(email: str) -> str | None:
    if not email:
        return None
    try:
        rows = await espo.list_all(
            "Lead",
            select=["id"],
            where=[{"type": "equals", "attribute": "emailAddress", "value": email}],
            max_items=1,
        )
        return rows[0].get("id") if rows else None
    except Exception:  # noqa: BLE001
        logger.exception("meeting-notes: lead lookup failed for %s", email[:3] + "***")
        return None


async def _contact_email_for_opp(opp: dict[str, Any]) -> str | None:
    """Best-effort attendee email — opp contacts, then account."""
    oid = opp.get("id") or ""
    try:
        rel = await espo.espo_get(
            f"/Opportunity/{oid}/contacts",
            params={"select": "id,emailAddress", "maxSize": 5},
        )
        for c in rel.get("list") or []:
            email = (c.get("emailAddress") or "").strip().lower()
            if email:
                return email
    except Exception:  # noqa: BLE001
        logger.exception("meeting-notes: contacts read failed for opp %s", oid)
    account_id = opp.get("accountId")
    if account_id:
        try:
            acc = await espo.espo_get(f"/Account/{account_id}", params={"select": "emailAddress"})
            email = (acc.get("emailAddress") or "").strip().lower()
            if email:
                return email
        except Exception:  # noqa: BLE001
            logger.exception("meeting-notes: account read failed for opp %s", oid)
    return None


async def _find_room_token(opp: dict[str, Any]) -> str | None:
    """Room token for an opportunity — opp notes first, then the linked
    Lead's notes (the Cal.com webhook writes the marker on the Lead)."""
    oid = opp.get("id") or ""
    token = _token_from_notes(await _opp_notes(oid))
    if token:
        return token
    email = await _contact_email_for_opp(opp)
    lead_id = await _lead_id_for_email(email or "")
    if not lead_id:
        return None
    lead_notes = await espo.list_all(
        "Note",
        select=["id", "post"],
        where=[
            {"type": "equals", "attribute": "parentType", "value": "Lead"},
            {"type": "equals", "attribute": "parentId", "value": lead_id},
        ],
        max_items=50,
    )
    return _token_from_notes(lead_notes)


async def provision_consult_room(*, name: str, email: str, start: str = "") -> dict[str, Any] | None:
    """Create — or reuse — the Talk room for a consultation booking.

    Called at booking time (site → /api/v1/talk/consult-room) and again
    from the Cal.com webhook; the ``talk_room:`` marker on the attendee's
    Lead notes is the dedupe key, so retries and double-fires never
    produce a second room. Returns ``{token, url, reused}`` or None.
    """
    if not talk.talk_enabled():
        return None
    email = (email or "").strip().lower()
    label = (name or email or "guest").strip()
    when = (start or "")[:10]
    room_name = f"Consultation — {label}" + (f" — {when}" if when else "")

    lead_id = await _lead_id_for_email(email)
    if lead_id:
        lead_notes = await espo.list_all(
            "Note",
            select=["id", "post"],
            where=[
                {"type": "equals", "attribute": "parentType", "value": "Lead"},
                {"type": "equals", "attribute": "parentId", "value": lead_id},
            ],
            max_items=50,
        )
        existing = _token_from_notes(lead_notes)
        if existing:
            return {"token": existing, "url": talk.room_url(existing), "reused": True}

    token = await talk.create_room(
        room_name[:180],
        description=(f"Cloudless consultation call.\nBooked via cloudless.gr — {email or 'guest'}.\nRecording enabled for internal meeting notes."),
    )
    if not token:
        return None
    link = talk.room_url(token)
    await talk.post_to_room(
        token,
        "👋 Consultation room — join with the video button. The call may be recorded for internal meeting notes.",
    )
    parts = name.strip().split() if name else []
    lead_id = await espo.upsert_lead(
        {
            "emailAddress": email,
            "firstName": parts[0] if parts else "",
            "lastName": " ".join(parts[1:]) if len(parts) > 1 else "",
            "source": "Web Site",
            "description": f"{ROOM_TAG}{token} — Cal.com booking join: {link}",
        }
    )
    if lead_id:
        await espo.add_lead_note(
            lead_id,
            f"{ROOM_TAG}{token} — join: {link}",
            entity="Lead",
        )
    return {"token": token, "url": link, "reused": False}


async def _create_consult_room(opp: dict[str, Any], email: str | None) -> str | None:
    name = (opp.get("name") or "Consultation").strip()[:180]
    when = str(opp.get("closeDate") or "")[:10]
    room_name = f"Consultation — {name}" + (f" — {when}" if when else "")
    token = await talk.create_room(
        room_name,
        description=(
            f"Cloudless consultation call.\nBooked via cloudless.gr — {email or 'guest'}.\nRecording enabled for meeting notes (ask before sharing externally)."
        ),
    )
    if not token:
        return None
    link = talk.room_url(token)
    await talk.post_to_room(
        token,
        "👋 Consultation room — join the call with the video button above. "
        "The call is recorded for internal meeting notes; let us know if you prefer not to be recorded.",
    )
    await espo.add_lead_note(
        str(opp.get("id") or ""),
        f"{ROOM_TAG}{token} — join: {link}",
        entity="Opportunity",
    )
    return token


REMINDER_TAG = "room_reminder:"


async def ensure_consult_rooms(*, session_factory=None) -> dict[str, Any]:
    """Give every upcoming ``appointmentsscheduled`` opportunity a Talk room,
    and nudge the room once on the meeting day so the moderator remembers
    to start the recording the minutes pipeline depends on."""
    if not _enabled():
        return {"status": "skipped", "reason": "talk_or_espocrm_not_configured"}
    factory = _factory(session_factory)
    today = date.today().isoformat()
    stats: dict[str, Any] = {"status": "ok", "created": 0, "existing": 0, "reminded": 0, "errors": 0}
    async with factory() as db:
        opps = await _opps_at_consult()
        for opp in opps:
            oid = opp.get("id") or ""
            close = str(opp.get("closeDate") or "")[:10]
            if not oid or not close or close < today:
                continue  # past meetings belong to process_meeting_notes
            try:
                notes = await _opp_notes(oid)
                token = _token_from_notes(notes)
                if not token:
                    email = await _contact_email_for_opp(opp)
                    token = await _create_consult_room(opp, email)
                    if token:
                        stats["created"] += 1
                    else:
                        stats["errors"] += 1
                        continue
                else:
                    stats["existing"] += 1
                # Same-day reminder — once per opportunity, marker-deduped,
                # and only during Athens daytime so it doesn't fire at 3 AM.
                athens_hour = datetime.now(ZoneInfo("Europe/Athens")).hour
                if close == today and 8 <= athens_hour < 21 and not any(REMINDER_TAG in (n.get("post") or "") for n in notes):
                    await talk.post_to_room(
                        token,
                        "⏰ Consultation call is today — please start the "
                        "Talk recording (record button) so meeting notes "
                        "are generated automatically afterwards.",
                    )
                    await espo.add_lead_note(
                        oid,
                        f"{REMINDER_TAG}{token} — same-day record reminder posted",
                        entity="Opportunity",
                    )
                    stats["reminded"] += 1
            except Exception:  # noqa: BLE001
                logger.exception("meeting-notes: room ensure failed for opp %s", oid)
                stats["errors"] += 1
        await db.commit()
    if stats["created"] or stats["reminded"]:
        logger.info("consult rooms sweep: %s", stats)
    return stats


# ── recording → transcript → minutes ────────────────────────────────────────


async def _extract_wav(video_bytes: bytes) -> bytes | None:
    """ffmpeg → 16 kHz mono WAV for Whisper. Returns None when ffmpeg is
    unavailable or conversion fails (caller falls back to raw upload)."""
    if not shutil.which("ffmpeg"):
        return None
    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as src:
        src.write(video_bytes)
        src_path = src.name
    out_path = src_path + ".wav"
    try:
        proc = await asyncio.create_subprocess_exec(
            "ffmpeg",
            "-y",
            "-i",
            src_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            out_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
        if proc.returncode != 0:
            return None
        with open(out_path, "rb") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        logger.exception("meeting-notes: ffmpeg extraction failed")
        return None
    finally:
        for p in (src_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


async def _transcribe(recording: tuple[bytes, str]) -> str | None:
    """Workers AI Whisper over the recording. Video containers are remuxed
    to WAV first; on ffmpeg failure we still try the raw bytes."""
    from app.services.inference import transcribe_workers_ai

    raw, ctype = recording
    wav = await _extract_wav(raw)
    if wav:
        try:
            result = await transcribe_workers_ai(wav, "audio/wav")
            text = str(result.get("text") or result.get("response") or "").strip()
            if text:
                return text
        except Exception:  # noqa: BLE001
            logger.exception("meeting-notes: whisper on wav failed — trying raw")
    audio_ctype = ctype if ctype.startswith("audio/") else "audio/mp4"
    try:
        result = await transcribe_workers_ai(raw, audio_ctype)
        return str(result.get("text") or result.get("response") or "").strip() or None
    except Exception:  # noqa: BLE001
        logger.exception("meeting-notes: transcription failed")
        return None


async def _summarize_minutes(transcript: str, opp: dict[str, Any], db: AsyncSession) -> str | None:
    from app.core.config import get_settings
    from app.services.inference import call_inference

    prompt = (
        "You are writing meeting minutes for a Cloudless consulting call.\n"
        f"Opportunity: {opp.get('name') or 'consultation'} ({opp.get('closeDate') or ''})\n\n"
        "From the transcript below, produce concise minutes in Markdown:\n"
        "## Summary\n(2-4 sentences: who, what was discussed, outcome)\n"
        "## Key points\n(bullets)\n"
        "## Decisions\n(bullets, or 'None recorded')\n"
        "## Action items\n(bullets with owner if clear, or 'None recorded')\n"
        "Rules: only state what the transcript supports — never invent names,\n"
        "numbers or commitments. If the transcript is too short or unrelated,\n"
        "say so plainly.\n\n"
        f"TRANSCRIPT:\n{transcript[:120000]}"
    )
    try:
        result = await call_inference(
            prompt=prompt,
            provider_name="dmr",
            db=db,
            model_override=get_settings().DMR_MID_MODEL,
            max_tokens=900,
        )
        text = result.get("response") or result.get("text") or ""
        if isinstance(text, list):
            text = "\n".join(str(p) for p in text)
        return str(text).strip() or None
    except Exception:  # noqa: BLE001
        logger.exception("meeting-notes: summarise failed")
        return None


async def process_meeting_notes(*, session_factory=None) -> dict[str, Any]:
    """Past consultations → Talk recording → transcript → CRM minutes."""
    if not _enabled():
        return {"status": "skipped", "reason": "talk_or_espocrm_not_configured"}
    factory = _factory(session_factory)
    today = date.today().isoformat()
    stats: dict[str, Any] = {
        "status": "ok",
        "checked": 0,
        "minutes_written": 0,
        "no_recording": 0,
        "no_room": 0,
        "errors": 0,
    }
    async with factory() as db:
        opps = await _opps_at_consult()
        for opp in opps:
            oid = opp.get("id") or ""
            close = str(opp.get("closeDate") or "")[:10]
            if not oid or not close or close >= today:
                continue
            try:
                if await _opp_has_marker(oid, NOTES_TAG):
                    continue
                stats["checked"] += 1
                token = await _find_room_token(opp)
                if not token:
                    stats["no_room"] += 1
                    continue
                messages = await talk.list_room_messages(token, limit=300)
                rec = talk.find_recording_file(messages)
                if not rec:
                    stats["no_recording"] += 1
                    # Shape-drift diagnostic: if the room HAS file messages
                    # but none matched the recording heuristic, record the
                    # observed file-param keys in the skip note so the real
                    # Talk recording shape is captured for the next fix.
                    file_keys = sorted(
                        {
                            k
                            for m in messages
                            for k in ((m.get("messageParameters") or {}).get("file") or {}).keys()
                            if isinstance((m.get("messageParameters") or {}).get("file"), dict)
                        }
                    )
                    drift = f"; file-params seen: {','.join(file_keys)}" if file_keys else ""
                    # Close the loop honestly — the room existed but no
                    # recording was shared; don't re-scan forever.
                    await espo.add_lead_note(
                        oid,
                        f"{NOTES_TAG} skipped — no recording shared in Talk room {token}{drift}",
                        entity="Opportunity",
                    )
                    continue
                got = await talk.download_talk_file(int(rec["file_id"]), path=rec.get("path"))
                if not got or len(got[0]) > _MAX_RECORDING_BYTES:
                    stats["errors"] += 1
                    continue
                transcript = await _transcribe(got)
                if not transcript:
                    stats["errors"] += 1
                    continue
                minutes = await _summarize_minutes(transcript, opp, db)
                if not minutes:
                    stats["errors"] += 1
                    continue
                note = f"{NOTES_TAG} Talk recording {rec['name']} → transcript ({len(transcript)} chars) → minutes:\n\n{minutes}"
                await espo.add_lead_note(oid, note[:9000], entity="Opportunity")
                stats["minutes_written"] += 1
                await talk.post_to_talk(
                    f"📝 Meeting notes ready — {opp.get('name') or oid} (Talk recording → transcript → EspoCRM).",
                    purpose="digest",
                )
            except Exception:  # noqa: BLE001
                logger.exception("meeting-notes: processing failed for opp %s", oid)
                stats["errors"] += 1
        await db.commit()
    if stats["minutes_written"] or stats["errors"]:
        logger.info("meeting notes sweep: %s", stats)
    return stats


async def pipeline_preflight() -> dict[str, Any]:
    """Cheap liveness probe of every stage — runs at the top of each sweep.

    A broken stage (Talk auth, EspoCRM, Whisper creds, ffmpeg, DMR) posts
    one line to the ops Talk room instead of failing silently mid-sweep.
    """
    checks: dict[str, bool] = {}
    degraded: list[str] = []

    checks["talk_enabled"] = talk.talk_enabled()
    checks["espo_configured"] = espo.configured()
    checks["ffmpeg"] = bool(shutil.which("ffmpeg"))
    from app.core.config import get_settings

    s = get_settings()
    checks["whisper_creds"] = bool(
        getattr(s, "CLOUDFLARE_ACCOUNT_ID", "") and (getattr(s, "CLOUDFLARE_AI_API_TOKEN", "") or getattr(s, "CLOUDFLARE_API_TOKEN", ""))
    )
    checks["dmr_model"] = bool(getattr(s, "DMR_MID_MODEL", ""))

    if checks["talk_enabled"]:
        checks["talk_api"] = await talk.ping()
    else:
        checks["talk_api"] = False

    for k, v in checks.items():
        if not v:
            degraded.append(k)
    if degraded:
        await talk.post_to_talk(
            "⚠️ Meeting-notes pipeline degraded — failed checks: " + ", ".join(degraded) + ". Consultations still get rooms; transcript/minutes paused.",
            purpose="alerts",
        )
    return {"ok": not degraded, "checks": checks, "degraded": degraded}


async def run_meeting_notes_sweep(*, session_factory=None) -> dict[str, Any]:
    """One pass: preflight → ensure upcoming rooms → process past meetings."""
    preflight = await pipeline_preflight()
    ensured = await ensure_consult_rooms(session_factory=session_factory)
    processed = await process_meeting_notes(session_factory=session_factory)
    return {"preflight": preflight, "ensure": ensured, "process": processed}

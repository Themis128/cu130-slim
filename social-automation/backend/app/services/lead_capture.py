from __future__ import annotations

import enum
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.lead import LeadCompanySize, LeadInterest, LeadSource
from app.services.leads import (
    coerce_company_size,
    coerce_interest,
    is_valid_email,
    upsert_lead,
)

logger = logging.getLogger(__name__)


class LeadCaptureStep(enum.StrEnum):
    name = "name"
    email = "email"
    company_size = "company_size"
    interest = "interest"
    done = "done"


@dataclass(frozen=True)
class LeadCaptureReply:
    text: str
    quick_replies: list[dict[str, Any]] | None = None
    completed_lead_id: uuid.UUID | None = None


def _is_greek_message(message: str) -> bool:
    return any(0x0370 <= ord(c) <= 0x03FF or 0x1F00 <= ord(c) <= 0x1FFF for c in (message or ""))


def _lead_prompts(*, greek: bool) -> dict[str, str]:
    if greek:
        return {
            "welcome": "Clear skies. Zero friction. Πριν συνεχίσουμε, να πάρουμε 30\" για να σας βοηθήσουμε σωστά.",
            "ask_name": "Ποιο είναι το όνομά σας;",
            "ask_email": "Ποιο email να χρησιμοποιήσουμε;",
            "bad_email": "Δεν φαίνεται σωστό email. Μπορείτε να το ξαναγράψετε;",
            "ask_company_size": "Πόσα άτομα είναι η ομάδα σας;",
            "ask_interest": "Τι σας ενδιαφέρει περισσότερο;",
            "thanks": "Τέλεια — τα πήραμε. Θα σας απαντήσουμε σύντομα.",
        }
    return {
        "welcome": "Clear skies. Zero friction. Quick 30 seconds so we can help properly.",
        "ask_name": "What’s your name?",
        "ask_email": "What email should we use?",
        "bad_email": "That doesn’t look like a valid email. Could you re-enter it?",
        "ask_company_size": "What’s your company size?",
        "ask_interest": "What are you most interested in?",
        "thanks": "Perfect — got it. We’ll get back to you shortly.",
    }


def _interest_quick_replies(*, greek: bool) -> list[dict[str, Any]]:
    if greek:
        return [
            {"content_type": "text", "title": "Cloud", "payload": "LEAD_INTEREST_CLOUD"},
            {"content_type": "text", "title": "Growth", "payload": "LEAD_INTEREST_GROWTH"},
            {"content_type": "text", "title": "Free audit", "payload": "LEAD_INTEREST_AUDIT"},
        ]
    return [
        {"content_type": "text", "title": "Cloud", "payload": "LEAD_INTEREST_CLOUD"},
        {"content_type": "text", "title": "Growth", "payload": "LEAD_INTEREST_GROWTH"},
        {"content_type": "text", "title": "Free audit", "payload": "LEAD_INTEREST_AUDIT"},
    ]


def _company_size_quick_replies(*, greek: bool) -> list[dict[str, Any]]:
    # Titles are intentionally short (Messenger UI truncates easily).
    return [
        {"content_type": "text", "title": "Solo", "payload": "LEAD_SIZE_SOLO"},
        {"content_type": "text", "title": "2–5", "payload": "LEAD_SIZE_2_5"},
        {"content_type": "text", "title": "6–20", "payload": "LEAD_SIZE_6_20"},
        {"content_type": "text", "title": "21–50", "payload": "LEAD_SIZE_21_50"},
        {"content_type": "text", "title": "51–200", "payload": "LEAD_SIZE_51_200"},
        {"content_type": "text", "title": "200+", "payload": "LEAD_SIZE_200_PLUS"},
    ]


def is_lead_capture_trigger(text: str, payload: str | None = None) -> bool:
    p = (payload or "").strip().upper()
    if p in {"GET_STARTED", "LEAD_CAPTURE_START", "MENU_CONTACT", "BOT_CONTACT"}:
        return True
    t = (text or "").strip().lower()
    return any(
        kw in t
        for kw in (
            "audit",
            "free audit",
            "book",
            "demo",
            "call me",
            "contact",
            "cloudless",
            "growth",
            "cloud",
            "συνεργασία",
            "audit",
            "ραντεβού",
            "επικοινωνία",
        )
    )


async def handle_lead_capture_message(
    db: AsyncSession,
    *,
    team_id: uuid.UUID,
    source: LeadSource,
    social_account_id: uuid.UUID | None,
    thread_id: str,
    inbound_text: str,
    postback_payload: str | None = None,
    meta_data: dict[str, Any] | None = None,
) -> LeadCaptureReply | None:
    """Lead capture state machine for Messenger/Instagram DMs (Redis-backed).

    Returns:
    - None if not in lead-capture mode (caller should continue normal bot flow)
    - LeadCaptureReply if handled (caller should send reply and stop bot flow)
    """
    settings = get_settings()
    redis_url = getattr(settings, "MESSENGER_REDIS_URL", None) or settings.REDIS_URL
    meta_data = meta_data or {}

    import redis.asyncio as aioredis

    key = f"lead:capture:{source.value}:{team_id}:{thread_id}"
    r = aioredis.from_url(redis_url, decode_responses=True)
    try:
        existing_raw = await r.get(key)
        state: dict[str, Any] = json.loads(existing_raw) if existing_raw else {}

        greek = _is_greek_message(inbound_text)
        prompts = _lead_prompts(greek=greek)

        # If not currently in lead capture, only start on trigger.
        if not state:
            if not is_lead_capture_trigger(inbound_text, postback_payload):
                return None
            state = {
                "step": LeadCaptureStep.name,
                "started_at": datetime.now(UTC).isoformat(),
                "lang": "el" if greek else "en",
                "fields": {},
            }
            await r.setex(key, 3600, json.dumps(state))  # 1h TTL
            return LeadCaptureReply(text=f"{prompts['welcome']}\n\n{prompts['ask_name']}")

        step = state.get("step") or LeadCaptureStep.name
        fields: dict[str, Any] = state.get("fields") or {}
        payload = (postback_payload or "").strip().upper()
        text = (inbound_text or "").strip()

        # Step: name
        if step == LeadCaptureStep.name:
            if text:
                fields["name"] = text[:200]
                state["fields"] = fields
                state["step"] = LeadCaptureStep.email
                await r.setex(key, 3600, json.dumps(state))
                return LeadCaptureReply(text=prompts["ask_email"])
            return LeadCaptureReply(text=prompts["ask_name"])

        # Step: email
        if step == LeadCaptureStep.email:
            email = text.lower()
            if email and is_valid_email(email):
                fields["email"] = email[:320]
                state["fields"] = fields
                state["step"] = LeadCaptureStep.company_size
                await r.setex(key, 3600, json.dumps(state))
                return LeadCaptureReply(
                    text=prompts["ask_company_size"],
                    quick_replies=_company_size_quick_replies(greek=greek),
                )
            return LeadCaptureReply(text=prompts["bad_email"])

        # Step: company size
        if step == LeadCaptureStep.company_size:
            size: LeadCompanySize | None = None
            if payload.startswith("LEAD_SIZE_"):
                mapping = {
                    "LEAD_SIZE_SOLO": "solo",
                    "LEAD_SIZE_2_5": "2-5",
                    "LEAD_SIZE_6_20": "6-20",
                    "LEAD_SIZE_21_50": "21-50",
                    "LEAD_SIZE_51_200": "51-200",
                    "LEAD_SIZE_200_PLUS": "200+",
                }
                size = coerce_company_size(mapping.get(payload))
            else:
                # Free-form input fallback
                size = coerce_company_size(text)
                if size is None and any(ch.isdigit() for ch in text):
                    t = text.replace(" ", "")
                    if t.startswith("1"):
                        size = LeadCompanySize.solo
                    elif "2" in t and "5" in t:
                        size = LeadCompanySize.s_2_5
                    elif "6" in t and "20" in t:
                        size = LeadCompanySize.s_6_20
                    elif "21" in t and "50" in t:
                        size = LeadCompanySize.s_21_50
                    elif "51" in t and "200" in t:
                        size = LeadCompanySize.s_51_200
                    elif "200" in t:
                        size = LeadCompanySize.s_200_plus

            if size is None:
                return LeadCaptureReply(
                    text=prompts["ask_company_size"],
                    quick_replies=_company_size_quick_replies(greek=greek),
                )

            fields["company_size"] = size.value
            state["fields"] = fields
            state["step"] = LeadCaptureStep.interest
            await r.setex(key, 3600, json.dumps(state))
            return LeadCaptureReply(
                text=prompts["ask_interest"],
                quick_replies=_interest_quick_replies(greek=greek),
            )

        # Step: interest
        if step == LeadCaptureStep.interest:
            interest: LeadInterest | None = None
            if payload.startswith("LEAD_INTEREST_"):
                mapping = {
                    "LEAD_INTEREST_CLOUD": "cloud",
                    "LEAD_INTEREST_GROWTH": "growth",
                    "LEAD_INTEREST_AUDIT": "audit",
                }
                interest = coerce_interest(mapping.get(payload))
            else:
                interest = coerce_interest(text)
                if interest is None:
                    t = text.lower()
                    if "audit" in t or "αξιολόγ" in t:
                        interest = LeadInterest.audit
                    elif "growth" in t or "marketing" in t or "ανάπτυξ" in t:
                        interest = LeadInterest.growth
                    elif "cloud" in t or "serverless" in t:
                        interest = LeadInterest.cloud

            if interest is None:
                return LeadCaptureReply(
                    text=prompts["ask_interest"],
                    quick_replies=_interest_quick_replies(greek=greek),
                )

            fields["interest"] = interest.value
            state["fields"] = fields
            state["step"] = LeadCaptureStep.done

            lead = await upsert_lead(
                db,
                team_id=team_id,
                source=source,
                social_account_id=social_account_id,
                thread_id=thread_id,
                name=str(fields.get("name") or "").strip(),
                email=str(fields.get("email") or "").strip(),
                company_size=coerce_company_size(fields.get("company_size")),
                interest=coerce_interest(fields.get("interest")),
                notes=None,
                meta_data={
                    "capture": {
                        "channel": source.value,
                        "thread_id": thread_id,
                        "session_started_at": state.get("started_at"),
                        "session_lang": state.get("lang"),
                    },
                    **meta_data,
                },
                dedupe_on_thread_id=True,
            )

            # Clear session; if the user re-triggers, start fresh.
            await r.delete(key)
            return LeadCaptureReply(text=prompts["thanks"], completed_lead_id=lead.id)

        # Defensive fallback: reset.
        await r.delete(key)
        return None
    finally:
        try:
            await r.aclose()
        except Exception:
            pass


"""Generic LinkedIn conversion ingest — site/worker events → Conversions API.

``POST /api/v1/webhooks/linkedin-conversion`` accepts a named event plus
identifiers and streams it through ``linkedin_advanced.stream_conversion``
using the rule resolved by ``resolve_conversion_rule`` (env → social_secrets,
auto-provisioned by ``ensure_linkedin_conversion_rules``).

Auth: shared secret ``LINKEDIN_CONVERSION_INGEST_KEY`` sent as
``X-Conversion-Key``. When unset the endpoint accepts (dev/local) — same
convention as the Cal.com webhook.
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/linkedin-conversion", tags=["webhooks"])


class ConversionIn(BaseModel):
    event: str = Field(min_length=2, max_length=64)
    email: str | None = None
    lead_id: str | None = None
    value: float | None = None
    currency: str | None = Field(default=None, max_length=8)
    event_id: str | None = Field(default=None, max_length=128)


def _authorized(request: Request) -> bool:
    key = os.environ.get("LINKEDIN_CONVERSION_INGEST_KEY", "")
    if not key:
        return True
    return hmac.compare_digest(request.headers.get("x-conversion-key", ""), key)


@router.post("")
async def ingest_conversion(body: ConversionIn, request: Request) -> Any:
    if not _authorized(request):
        raise HTTPException(status_code=401, detail="bad ingest key")

    from app.services import linkedin_advanced

    if body.event not in linkedin_advanced.CONVERSION_EVENTS:
        return JSONResponse({"ok": True, "skipped": f"unknown event {body.event!r}", "known": sorted(linkedin_advanced.CONVERSION_EVENTS)})

    rule = await linkedin_advanced.resolve_conversion_rule(body.event)
    if not rule:
        return JSONResponse({"ok": False, "error": "no conversion rule resolved", "event": body.event}, status_code=409)

    sha = hashlib.sha256(body.email.strip().lower().encode()).hexdigest() if body.email else None
    out = await linkedin_advanced.stream_conversion(
        conversion_rule=rule,
        happened_at_ms=int(time.time() * 1000),
        email_sha256=sha,
        lead_id=body.lead_id,
        conversion_value=body.value,
        currency_code=body.currency,
    )
    logger.info("linkedin-conversion %s → %s", body.event, out)
    return JSONResponse({"ok": bool(out.get("ok")), "event": body.event, "conversion": out})

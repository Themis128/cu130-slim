"""LinkedIn P1 tasks — lead routing + winner boost (behind LINKEDIN_ADVANCED).

- ``route_linkedin_leads``: poll lead-form responses → dedupe → route into the
  audit funnel (CRM + follow-up + Slack alert).
- ``boost_linkedin_winners``: pick up ``boost_candidate`` posts flagged by
  linkedin_engagement_score → sponsored-content campaign against promo credit
  only (standing rule: never real spend).
"""

import logging
import os
from datetime import UTC, datetime

from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

ENABLED = os.getenv("LINKEDIN_ADVANCED", "0") == "1"


@celery_app.task(name="app.worker.tasks.linkedin_lead_boost.route_linkedin_leads")
def route_linkedin_leads() -> dict:
    return run_async(_route_linkedin_leads())


async def _webhook_selfcheck() -> None:
    """Probe our own Lead Sync webhook challenge endpoint.

    LinkedIn re-validates webhook URLs periodically and blocks delivery
    after repeated failures (seen 2026-11-16 when an infra flap made the
    endpoint unreachable). A ~200ms GET every 30min catches the outage
    before LinkedIn's email does.
    """
    import secrets

    import httpx

    from app.core.config import settings
    from app.services.slack_notifications import send_telegram_alert

    base = (settings.TELEGRAM_WEBHOOK_BASE or "").rstrip("/")
    if not base:
        return
    challenge = f"selfcheck-{secrets.token_hex(8)}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(
                f"{base}/webhooks/linkedin-lead-sync",
                params={"challengeCode": challenge},
            )
        ok = r.status_code == 200 and challenge in r.text
    except httpx.HTTPError:
        ok = False
    if not ok:
        await send_telegram_alert(
            "P2",
            f"linkedin-lead-sync webhook self-check failed "
            f"(LinkedIn will block delivery if validation keeps failing): "
            f"GET {base}/webhooks/linkedin-lead-sync?challengeCode=…",
        )


async def _route_linkedin_leads() -> dict:
    if not ENABLED:
        return {"skip": "LINKEDIN_ADVANCED off"}
    await _webhook_selfcheck()
    from sqlalchemy import select

    from app.models.social_account import SocialAccount
    from app.services import linkedin_ads

    async with task_session() as db:
        acc = (
            (
                await db.execute(
                    select(SocialAccount).where(
                        SocialAccount.platform == "linkedin",
                        SocialAccount.account_type == "organization",
                    )
                )
            )
            .scalars()
            .first()
        )
        if not acc:
            return {"skip": "no-linkedin-org-account"}
        accounts = await linkedin_ads.ad_accounts(db, str(acc.team_id))

    leads: list[dict] = []
    for a in accounts:
        acct_urn = a.get("urn") or f"urn:li:sponsoredAccount:{a.get('id')}"
        for form in await linkedin_ads.lead_gen_forms(db, acct_urn):
            responses = await linkedin_ads.lead_form_responses(db, acct_urn)
            for r in responses:
                leads.append({"account": acct_urn, "form": form.get("id"), "response": r})
    if not leads:
        return {"routed": 0}

    from app.services.slack_notifications import post_alert_to_slack

    routed = 0
    for lead in leads[:25]:
        if await route_lead_response(lead.get("response") or {}, lead.get("form")):
            routed += 1
    if routed:
        await post_alert_to_slack(f":new: LinkedIn lead sync: {routed} lead(s) routed to EspoCRM")
    return {"routed": routed, "found": len(leads)}


async def route_lead_response(resp: dict, form_id: str | None = None) -> bool:
    """Route ONE leadFormResponse → EspoCRM + lead_created conversion.

    Shared by the polling task and the Lead Sync webhook receiver. Returns
    True when a CRM lead was created/updated.
    """
    from app.services import espocrm_client, linkedin_advanced

    answers = _lead_answers(resp, await _question_map(resp, form_id))
    low = {str(k).lower(): v for k, v in answers.items()}
    email = (low.get("email") or low.get("emailaddress") or "").strip()
    if not email:
        return False
    first = (low.get("firstname") or "").strip()
    last = (low.get("lastname") or "").strip()
    lead_id = await espocrm_client.upsert_lead(
        {
            "emailAddress": email,
            "firstName": first,
            "lastName": last,
            "source": "Social Media",
            "description": f"LinkedIn lead-gen response {resp.get('id', '')} via {form_id or 'unknown'}",
        }
    )
    if not lead_id:
        return False
    try:
        import hashlib

        sha = hashlib.sha256(email.strip().lower().encode()).hexdigest()
        rule = await linkedin_advanced.resolve_conversion_rule("lead_created") or os.environ.get("LINKEDIN_CONVERSION_RULE_LEAD_CREATED", "lead_created")
        conv = await linkedin_advanced.stream_conversion(
            conversion_rule=rule,
            happened_at_ms=int(datetime.now(UTC).timestamp() * 1000),
            email_sha256=sha,
            lead_id=str(resp.get("id") or "") or None,
        )
        if not conv.get("ok") and conv.get("status") not in (None, 200, 201):
            logger.info("lead_created conversion not accepted: %s", conv)
    except Exception as exc:  # noqa: BLE001 — conversion reporting never blocks routing
        logger.warning("lead_created conversion failed: %s", exc)
    return True


async def _question_map(resp: dict, form_id: str | None) -> dict:
    """questionId → {keys,label,options} via GET /rest/leadForms/{id}.

    Form ref comes from the response's ``versionedLeadGenFormUrn`` or the
    caller-supplied ``form_id``; failures degrade to id-keyed answers.
    """
    from app.services import linkedin_advanced

    ref = resp.get("versionedLeadGenFormUrn") or form_id
    if not ref:
        return {}
    form = await linkedin_advanced.get_lead_form(str(ref))
    return linkedin_advanced.lead_form_question_map(form)


def _lead_answers(resp: dict, qmap: dict | None = None) -> dict[str, str]:
    """Normalize LinkedIn leadFormResponses to {field: value}.

    Real API shape: ``formResponse.answers[]`` items carry ``questionId`` +
    ``answerDetails`` (``textQuestionAnswer.answer`` /
    ``multipleChoiceAnswer.options`` id list). ``qmap`` (from
    ``lead_form_question_map``) resolves questionId → CRM-friendly keys
    (``name``/``predefinedField`` like ``email``, ``firstName``) and
    multiple-choice option ids → labels. Without a map, keys fall back to
    ``question_{id}`` and raw option ids.
    """
    qmap = qmap or {}
    out: dict[str, str] = {}

    def _emit(qid, value: str) -> None:
        meta = qmap.get(int(qid)) if str(qid).isdigit() else None
        keys = (meta or {}).get("keys") or set()
        label = (meta or {}).get("label") or f"question_{qid}"
        for k in keys or {label.lower()}:
            out.setdefault(k, value)
        out.setdefault(label, value)

    fr = resp.get("formResponse") or {}
    if isinstance(fr, list):
        # Flat {question, answer} payloads — normalize into top-level
        # answers so the flat-shape pass below keys them by question.
        resp = {**resp, "answers": fr}
        fr = {}
    answers = fr.get("answers") if isinstance(fr, dict) else None
    for item in answers or resp.get("answers") or []:
        if not isinstance(item, dict):
            continue
        qid = item.get("questionId", item.get("question"))
        details = item.get("answerDetails") or {}
        value = ""
        if "textQuestionAnswer" in details:
            value = str((details["textQuestionAnswer"] or {}).get("answer") or "")
        elif "multipleChoiceAnswer" in details:
            opts = (details["multipleChoiceAnswer"] or {}).get("options") or []
            meta = qmap.get(int(qid)) if str(qid).isdigit() else None
            labels = []
            for o in opts:
                hit = next((op["text"] for op in (meta or {}).get("options", []) if str(op.get("id")) == str(o)), None)
                if hit is None and str(qid).isdigit():
                    # 1-based positional fallback into the ordered options list
                    try:
                        hit = ((meta or {}).get("options") or [])[int(o) - 1]["text"]
                    except (IndexError, KeyError, TypeError, ValueError):
                        hit = None
                labels.append(hit if hit is not None else str(o))
            value = "; ".join(labels)
        elif "answer" in item:
            a = item["answer"]
            value = "; ".join(str(x) for x in a) if isinstance(a, list) else str(a or "")
        elif details:
            # Unknown answerDetails shape — keep the raw payload readable.
            value = str(details)[:300]
        if value:
            _emit(qid, value)

    # Flat shapes (custom-question maps / legacy poller payloads).
    for item in resp.get("answers") or []:
        if isinstance(item, dict) and "question" in item:
            q = str(item.get("question") or "")
            a = item.get("answer")
            if isinstance(a, list):
                a = "; ".join(str(x) for x in a)
            out[q] = str(a or "")
    for k, v in (resp.get("customQuestions") or {}).items():
        out.setdefault(str(k), str(v))
    return out


@celery_app.task(name="app.worker.tasks.linkedin_lead_boost.boost_linkedin_winners")
def boost_linkedin_winners() -> dict:
    return run_async(_boost_linkedin_winners())


async def _boost_linkedin_winners() -> dict:
    if not ENABLED:
        return {"skip": "LINKEDIN_ADVANCED off"}
    from sqlalchemy import select

    from app.models.content import Post
    from app.services.slack_notifications import post_alert_to_slack

    async with task_session() as db:
        winners = (await db.execute(select(Post).where(Post.meta_data["boost_candidate"].astext == "true"))).scalars().all()
        for p in winners:
            p.meta_data = {**(p.meta_data or {}), "boost_candidate": False, "boost_queued": True}
        await db.commit()
    if winners:
        await post_alert_to_slack(f":rocket: {len(winners)} LinkedIn posts flagged >2σ — promo-credit boost queued")
    return {"boosted": len(winners)}

"""LinkedIn Page invite-to-follow via the browser bridge + NLP scoring.

Company Pages get monthly invite credits (250/mo → 50/mo announced).
Credits expire unused at the monthly refill, so a scheduled task sends
a daily batch until the pool is empty, then idles until refill.

Selection strategy (data-driven, NLP):
1. Harvest every candidate row (name + headline) across the paginated list.
2. Score all candidates in ONE DMR inference call for "likelihood to
   accept AND relevance to cloudless.gr" (cloud infra, DevOps, B2B SaaS,
   Greek/EMEA tech market).
3. Invite the top-N by score — not raw list order, not keywords.
   Falls back to LinkedIn's own ranking order if inference fails.

Verified live 2026-09-23 on the cloudless.gr page (org 108614163).
"""
import asyncio
import json
import logging
import re
from typing import Any, TypedDict

from app.core.config import get_settings
from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError
from app.services.browser_orchestrator import browser_session

logger = logging.getLogger(__name__)

# cloudless.gr Company Page organization id (single-tenant platform).
_ORG_ID = "108614163"
_PAGE_POSTS_URL = f"https://www.linkedin.com/company/{_ORG_ID}/admin/page-posts/"

_RESULTS_CONTAINER = ".invitee-picker__results-container"
_SHOW_MORE_TEXT = "Show more results"
_MAX_PAGINATION_ROUNDS = 15
_MAX_CANDIDATES = 260  # one monthly pool + headroom


class _Candidate(TypedDict):
    i: int
    name: str
    headline: str

_SCORE_PROMPT = """You are ranking LinkedIn connections for a Page-follow invitation.

Page: cloudless.gr — a Greek cloud-infrastructure / managed-cloud company.
Content: Cloudflare, DevOps, Kubernetes, serverless, cloud cost, security, B2B SaaS.
Buyer profile: engineers, DevOps/SRE, CTOs/founders, IT managers, cloud/FinOps,
software consultants, tech executives — especially Greece/EMEA.

Score EACH candidate 0-100 for likelihood to ACCEPT the invite AND find the
page relevant. Heavily weight: cloud/devops/software roles, technical
seniority, founders/CTOs, Greek tech professionals, cloud vendors/partners.
Lightly penalize: pure HR/recruiting, unrelated industries, students.

Return ONLY JSON: {"scores": [{"i": <index>, "s": <0-100>}, ...]}
one entry per candidate, same indices given.

Candidates:
"""


async def _eval(client: BrowserBridgeClient, expression: str) -> Any:
    res = await client.evaluate(expression)
    raw = res.get("result") if isinstance(res, dict) else res
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return raw
    return raw


async def _click_button_by_text(client: BrowserBridgeClient, text: str, starts_with: bool = False) -> bool:
    cmp_expr = f"(e.innerText||'').trim().startsWith('{text}')" if starts_with else f"(e.innerText||'').trim()==='{text}'"
    coords = await _eval(
        client,
        "(()=>{const b=[...document.querySelectorAll('button')]"
        f".find(e=>e.offsetParent&&{cmp_expr});"
        "if(!b) return null;"
        "b.scrollIntoView({block:'center'});"
        "const r=b.getBoundingClientRect();"
        "return {x:Math.round(r.x+r.width/2),y:Math.round(r.y+r.height/2)}})()",
    )
    if not coords or not isinstance(coords, dict):
        return False
    await client.mouse_click(coords["x"], coords["y"])
    return True


async def _harvest_candidates(
    client: BrowserBridgeClient,
    renew: Any = None,
) -> list[_Candidate]:
    """Paginate the invite list and collect every row's name + headline.

    Returns [{i, name, headline}] in LinkedIn's ranking order (the fallback
    ordering if NLP scoring fails). ``renew`` is an optional coroutine that
    extends the orchestrator lock across long pagination runs.
    """
    candidates: list[_Candidate] = []
    seen: set[str] = set()
    stale_rounds = 0
    for _ in range(_MAX_PAGINATION_ROUNDS):
        if renew is not None:
            await renew()
        rows = await _eval(
            client,
            f"(()=>{{const l=document.querySelector('{_RESULTS_CONTAINER}');"
            "if(!l) return [];"
            "return [...l.querySelectorAll('input[type=checkbox]')].map(c=>{"
            "const r=c.closest('li,div');"
            "const t=(r?r.innerText:'').split('\\n').map(s=>s.trim()).filter(Boolean);"
            "return {name:t[0]||'',headline:t[1]||''}})})()",
        )
        if isinstance(rows, list):
            for r in rows:
                key = r.get("name", "") + "|" + r.get("headline", "")
                if r.get("name") and key not in seen:
                    seen.add(key)
                    candidates.append({"i": len(candidates), "name": r["name"], "headline": r.get("headline", "")})
        if len(candidates) >= _MAX_CANDIDATES:
            break
        # Scroll + paginate.
        await _eval(
            client,
            f"(()=>{{const l=document.querySelector('{_RESULTS_CONTAINER}');"
            "if(l) l.scrollTop=l.scrollHeight; return true})()",
        )
        if not await _click_button_by_text(client, _SHOW_MORE_TEXT):
            break
        await asyncio.sleep(4)
        new_len = await _eval(
            client,
            f"document.querySelectorAll('{_RESULTS_CONTAINER} input[type=checkbox]').length",
        )
        # Detect pagination stalls (2 rounds with no new rows).
        stale_rounds = stale_rounds + 1 if (new_len or 0) <= len(candidates) + 5 else 0
        if stale_rounds >= 2:
            break
    return candidates


async def _score_candidates(candidates: list[_Candidate]) -> dict[int, int]:
    """NLP-score all candidates in one DMR call. Returns {index: score}."""
    if not candidates:
        return {}
    listing = "\n".join(f"{c['i']}. {c['name']} — {c['headline']}" for c in candidates)
    try:
        from app.services.inference import call_inference

        res = await call_inference(
            prompt=_SCORE_PROMPT + listing,
            provider_name="dmr",
            platform="linkedin",
            schema={
                "type": "object",
                "properties": {
                    "scores": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"i": {"type": "integer"}, "s": {"type": "integer"}},
                            "required": ["i", "s"],
                        },
                    }
                },
                "required": ["scores"],
            },
        )
        payload = res.get("response", res)
        if isinstance(payload, str):
            payload = json.loads(payload)
        scores = {
            int(e["i"]): int(e["s"])
            for e in (payload.get("scores") or [])
            if isinstance(e, dict) and "i" in e and "s" in e
        }
        logger.info("linkedin invites: NLP scored %s/%s candidates", len(scores), len(candidates))
        return scores
    except Exception as exc:  # noqa: BLE001 — fall back to list order
        logger.warning("linkedin invites: NLP scoring failed (%s) — using list order", exc)
        return {}


async def _click_checkbox_at_index(client: BrowserBridgeClient, idx: int) -> bool:
    return bool(
        await _eval(
            client,
            f"(()=>{{const l=document.querySelector('{_RESULTS_CONTAINER}');"
            "const cbs=[...l.querySelectorAll('input[type=checkbox]')];"
            f"const c=cbs[{idx}]; if(!c||c.checked) return false;"
            "c.scrollIntoView({block:'center'}); c.click(); return true})()",
        )
    )


async def _selected_count(client: BrowserBridgeClient) -> int:
    res = await _eval(
        client,
        "(document.body.innerText.match(/\\d+ selected/)||['0 selected'])[0]",
    )
    m = re.search(r"\d+", str(res))
    return int(m.group()) if m else 0


async def _open_invite_dialog(client: BrowserBridgeClient) -> bool:
    """Navigate to the page admin and open the Invite connections dialog."""
    await client.navigate(_PAGE_POSTS_URL)
    await asyncio.sleep(8)
    clicked = await client.click("button", "Invite connections")
    if not clicked or clicked.get("detail"):
        return False
    await asyncio.sleep(6)
    for _ in range(10):
        if await _eval(client, f"!!document.querySelector('{_RESULTS_CONTAINER}')"):
            return True
        await asyncio.sleep(2)
    return False


async def _dialog_open(client: BrowserBridgeClient) -> bool:
    try:
        return bool(await _eval(client, f"!!document.querySelector('{_RESULTS_CONTAINER}')"))
    except BrowserBridgeError:
        return False


async def _read_credits(client: BrowserBridgeClient, default: int) -> int:
    credit_txt = await _eval(
        client,
        "(document.body.innerText.match(/\\d+\\/\\d+ credits available/)||[''])[0]",
    )
    m = re.search(r"(\d+)/(\d+)", str(credit_txt))
    return int(m.group(1)) if m else default


async def send_invite_batch(batch_size: int = 40) -> dict[str, Any]:
    """Send one batch of LinkedIn Page follow invitations.

    Two short orchestrator lock-holds around the browser with DMR scoring in
    between — the cooperative lock covers ~90s, so the browser is released
    while inference runs and re-acquired for selection + send.

    Flow: claim session → invite dialog → credits → harvest all candidates
    → (release) NLP-score → (re-claim) select top-scored → Invite N → verify.

    Never raises — scheduled daily, failures must not cascade.
    """
    settings = get_settings()
    client = BrowserBridgeClient(settings.BROWSER_BRIDGE_URL, platform="linkedin")
    result: dict[str, Any] = {"status": "error", "sent": 0}

    # ── Phase A (locked): claim browser, open dialog, harvest candidates ──
    try:
        session = browser_session("linkedin", client, max_wait=90)
        async with session:
            try:
                await client.start_session("linkedin", contention_retries=14)
            except BrowserBridgeError:
                # Rotating pollers won every hold-gap — this task runs once
                # daily with expiring credits, so preempt as a last resort.
                await client.start_session("linkedin", force=True)
            await asyncio.sleep(5)

            url = await _eval(client, "location.href")
            if isinstance(url, str) and ("/login" in url or "checkpoint" in url):
                result.update(status="skipped", reason="linkedin_session_not_logged_in")
                return result

            if not await _open_invite_dialog(client):
                result.update(status="skipped", reason="invite_dialog_missing")
                return result

            credits_left = await _read_credits(client, batch_size)
            result["credits_available"] = credits_left
            if credits_left <= 0:
                result.update(status="skipped", reason="no_credits")
                return result
            target = min(batch_size, credits_left)

            candidates = await _harvest_candidates(client, session.renew)
            result["candidates"] = len(candidates)
            if not candidates:
                result.update(status="skipped", reason="no_candidates")
                return result
    except BrowserBridgeError as exc:
        result.update(reason=f"bridge:{exc.status_code}", detail=str(exc)[:300])
        return result
    except Exception as exc:  # noqa: BLE001 — scheduled task must not propagate
        logger.exception("linkedin invite harvest failed")
        result.update(reason=type(exc).__name__, detail=str(exc)[:300])
        return result

    # ── Phase B (unlocked): NLP scoring — no browser needed ──
    scores = await _score_candidates(candidates)
    if scores:
        ranked = sorted(candidates, key=lambda c: (-scores.get(c["i"], 0), c["i"]))
    else:
        ranked = candidates  # fallback: LinkedIn's own ranking order
    picks = ranked[:target]
    result["top_scores"] = [scores.get(c["i"]) for c in picks[:10]]

    # ── Phase C (locked): re-claim, select picks, send ──
    try:
        session = browser_session("linkedin", client, max_wait=90)
        async with session:
            try:
                await client.start_session("linkedin", contention_retries=6)
            except BrowserBridgeError:
                await client.start_session("linkedin", force=True)
            await asyncio.sleep(3)
            if not await _dialog_open(client) and not await _open_invite_dialog(client):
                result.update(status="skipped", reason="invite_dialog_lost")
                return result

            # Click the chosen checkboxes. The list is virtualized, so resolve
            # each pick's DOM position by matching name text at click time.
            clicked_count = 0
            for c in picks:
                await session.renew()
                try:
                    ok = await _eval(
                        client,
                        f"(()=>{{const l=document.querySelector('{_RESULTS_CONTAINER}');"
                        "const rows=[...l.querySelectorAll('input[type=checkbox]')];"
                        "const c=rows.find(cb=>{const r=cb.closest('li,div');"
                        "const t=(r?r.innerText:'');"
                        f"return t.includes({json.dumps(c['name'])})}});"
                        "if(!c||c.checked) return false;"
                        "c.scrollIntoView({block:'center'}); c.click(); return true})()",
                    )
                except BrowserBridgeError as exc:
                    if exc.status_code != 409:
                        continue
                    # Force-preempted — re-claim once and verify the invite
                    # dialog survived; otherwise abort to the next run.
                    try:
                        await client.start_session("linkedin", contention_retries=4)
                    except BrowserBridgeError:
                        result.update(
                            status="skipped", reason="browser_session_stolen",
                            picked_clicked=clicked_count,
                        )
                        return result
                    await asyncio.sleep(3)
                    if not await _dialog_open(client):
                        result.update(
                            status="skipped", reason="invite_dialog_lost",
                            picked_clicked=clicked_count,
                        )
                        return result
                    continue
                if ok:
                    clicked_count += 1
                await asyncio.sleep(0.5)

            selected = await _selected_count(client)
            result["selected"] = selected
            result["picked_clicked"] = clicked_count
            if selected == 0:
                result.update(status="skipped", reason="nothing_selected")
                return result

            if not await _click_button_by_text(client, "Invite ", starts_with=True):
                result.update(reason="invite_send_button_missing")
                return result
            await asyncio.sleep(8)

            body = await _eval(client, "document.body.innerText.slice(0,3000)")
            if isinstance(body, str) and ("Invitations sent" in body or "invited to follow" in body):
                result.update(status="sent", sent=selected)
                logger.info(
                    "LinkedIn invites sent: %s of %s candidates (credits were %s)",
                    selected, len(candidates), credits_left,
                )
                return result

            result.update(reason="send_unconfirmed", detail=str(body)[:300])
            return result

    except BrowserBridgeError as exc:
        result.update(reason=f"bridge:{exc.status_code}", detail=str(exc)[:300])
    except Exception as exc:  # noqa: BLE001 — scheduled task must not propagate
        logger.exception("linkedin invite batch failed")
        result.update(reason=type(exc).__name__, detail=str(exc)[:300])
    return result

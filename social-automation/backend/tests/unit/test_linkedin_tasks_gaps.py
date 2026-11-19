"""Coverage for linkedin_lead_boost + linkedin_conversion_ensure edge paths."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.worker.tasks import linkedin_conversion_ensure as ENS
from app.worker.tasks import linkedin_lead_boost as LB


class _AsyncCM:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, *a):
        return False


# ── _route_linkedin_leads: no org account ───────────────────────────────


@pytest.mark.asyncio
async def test_route_leads_no_org_account(monkeypatch):
    monkeypatch.setattr(LB, "ENABLED", True)
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))))
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))
    out = await LB._route_linkedin_leads()
    assert out == {"skip": "no-linkedin-org-account"}


# ── route_lead_response branches ────────────────────────────────────────


@pytest.mark.asyncio
async def test_route_lead_response_no_email_and_no_lead_id(monkeypatch):
    import app.services.espocrm_client as ESPO

    monkeypatch.setattr(ESPO, "upsert_lead", AsyncMock(return_value=None))
    out = await LB.route_lead_response({"id": "r", "answers": [{"question": "name", "answer": "X"}]})
    assert out is False

    monkeypatch.setattr(ESPO, "upsert_lead", AsyncMock(return_value="lead-1"))
    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "resolve_conversion_rule", AsyncMock(return_value="urn:rule:1"))
    conv = AsyncMock(return_value={"ok": False, "status": 400})
    monkeypatch.setattr(adv, "stream_conversion", conv)
    resp = {"id": "r9", "answers": [{"question": "email", "answer": "e@x.com"}]}
    out = await LB.route_lead_response(resp)
    assert out is True
    conv.assert_awaited_once()


# ── _question_map early return ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_question_map_no_ref_returns_empty():
    out = await LB._question_map({}, None)
    assert out == {}


# ── _lead_answers edge shapes ───────────────────────────────────────────


def test_lead_answers_skips_non_dict_and_unknown_details():
    resp = {
        "answers": [
            "junk",
            {"questionId": 7, "answerDetails": {"mysteryShape": {"x": 1}}},
            {"questionId": 8},
        ]
    }
    out = LB._lead_answers(resp)
    assert "question_7" in out
    assert "question_8" not in out  # no value emitted


def test_lead_answers_multiple_choice_positional_and_fallback():
    qmap = {3: {"keys": {"service"}, "label": "Service", "options": [{"id": "o1", "text": "A"}, {"id": "o2", "text": "B"}]}}
    resp = {
        "answers": [
            {"questionId": 3, "answerDetails": {"multipleChoiceAnswer": {"options": ["o2", "9"]}}},
            {"questionId": 4, "answerDetails": {"multipleChoiceAnswer": {"options": ["x"]}}},
        ]
    }
    out = LB._lead_answers(resp, qmap)
    assert out["Service"] == "B; 9"
    assert out["question_4"] == "x"


def test_lead_answers_flat_list_answer_and_custom_questions():
    resp = {
        "answers": [
            {"question": "email", "answer": ["a@x.com", "b@x.com"]},
            {"no_question_key": True},
        ],
        "customQuestions": {"budget": "500"},
    }
    out = LB._lead_answers(resp)
    assert out["email"] == "a@x.com; b@x.com"
    assert out["budget"] == "500"


# ── linkedin_conversion_ensure ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_ensure_ctx_missing_and_list_error(monkeypatch):
    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=None))
    out = await ENS._ensure()
    assert out == {"skip": "linkedin-advanced-disabled"}

    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=("tok", "urn")))
    monkeypatch.setattr(adv, "list_conversion_rules", AsyncMock(return_value={"status": 500}))
    alert = AsyncMock()
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", alert)
    out = await ENS._ensure()
    assert out == {"error": 500}
    alert.assert_awaited_once()


@pytest.mark.asyncio
async def test_ensure_found_by_name_and_create_error(monkeypatch):
    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=("tok", "urn")))
    monkeypatch.setattr(adv, "CONVERSION_EVENTS", {"evt_a": ("ENV_A", "LEAD"), "evt_b": ("ENV_B", "LEAD")})
    monkeypatch.setattr(
        adv,
        "list_conversion_rules",
        AsyncMock(return_value={"status": 200, "rules": [{"id": "77", "name": "evt_a"}]}),
    )

    async def _resolve(event):
        return "urn:existing" if event == "evt_a" else None

    monkeypatch.setattr(adv, "resolve_conversion_rule", _resolve)
    monkeypatch.setattr(adv, "create_conversion_rule", AsyncMock(return_value={"error": "nope"}))
    sets = []

    class _SS:
        async def set(self, k, v, note):
            sets.append((k, v))

    monkeypatch.setattr("app.services.secret_store.secret_store", _SS())
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock())
    out = await ENS._ensure()
    assert "evt_a" in out["found"]
    assert out["errors"]
    assert sets == []


@pytest.mark.asyncio
async def test_ensure_creates_and_persists_rule(monkeypatch):
    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=("tok", "urn")))
    monkeypatch.setattr(adv, "CONVERSION_EVENTS", {"evt_c": ("ENV_C", "LEAD")})
    monkeypatch.setattr(adv, "list_conversion_rules", AsyncMock(return_value={"status": 200, "rules": []}))
    monkeypatch.setattr(adv, "resolve_conversion_rule", AsyncMock(return_value=None))
    monkeypatch.setattr(
        adv,
        "create_conversion_rule",
        AsyncMock(return_value={"rule_urn": "urn:lla:llaPartnerConversion:88"}),
    )
    sets = []

    class _SS:
        async def set(self, k, v, note):
            sets.append((k, v))

    monkeypatch.setattr("app.services.secret_store.secret_store", _SS())
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock())
    out = await ENS._ensure()
    assert out["created"] == [{"event": "evt_c", "rule": "urn:lla:llaPartnerConversion:88"}]
    assert sets == [("ENV_C", "urn:lla:llaPartnerConversion:88")]


def test_ensure_task_wrapper(monkeypatch):
    monkeypatch.setattr(ENS, "run_async", lambda c: {"wrapped": True})
    assert ENS.ensure_linkedin_conversion_rules() == {"wrapped": True}


@pytest.mark.asyncio
async def test_route_lead_response_upsert_fails(monkeypatch):
    import app.services.espocrm_client as ESPO

    monkeypatch.setattr(ESPO, "upsert_lead", AsyncMock(return_value=None))
    resp = {"id": "r", "answers": [{"question": "email", "answer": "e@x.com"}]}
    assert await LB.route_lead_response(resp) is False


@pytest.mark.asyncio
async def test_ensure_finds_rule_by_name_in_list(monkeypatch):
    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=("tok", "urn")))
    monkeypatch.setattr(adv, "CONVERSION_EVENTS", {"evt_d": ("ENV_D", "LEAD")})
    monkeypatch.setattr(
        adv,
        "list_conversion_rules",
        AsyncMock(return_value={"status": 200, "rules": [{"id": "55", "name": "evt_d"}]}),
    )
    monkeypatch.setattr(adv, "resolve_conversion_rule", AsyncMock(return_value=None))
    sets = []

    class _SS:
        async def set(self, k, v, note):
            sets.append((k, v))

    monkeypatch.setattr("app.services.secret_store.secret_store", _SS())
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock())
    out = await ENS._ensure()
    assert "evt_d" in out["found"]
    assert sets == [("ENV_D", "urn:lla:llaPartnerConversion:55")]


@pytest.mark.asyncio
async def test_ensure_named_rule_without_id_falls_to_create(monkeypatch):
    adv = __import__("app.services.linkedin_advanced", fromlist=["x"])
    monkeypatch.setattr(adv, "_ctx", AsyncMock(return_value=("tok", "urn")))
    monkeypatch.setattr(adv, "CONVERSION_EVENTS", {"evt_e": ("ENV_E", "LEAD")})
    monkeypatch.setattr(
        adv,
        "list_conversion_rules",
        AsyncMock(return_value={"status": 200, "rules": [{"name": "evt_e"}]}),  # no id
    )
    monkeypatch.setattr(adv, "resolve_conversion_rule", AsyncMock(return_value=None))
    monkeypatch.setattr(
        adv,
        "create_conversion_rule",
        AsyncMock(return_value={"rule_urn": "urn:lla:llaPartnerConversion:99"}),
    )
    monkeypatch.setattr("app.services.secret_store.secret_store", SimpleNamespace(set=AsyncMock()))
    monkeypatch.setattr("app.services.slack_notifications.post_alert_to_slack", AsyncMock())
    out = await ENS._ensure()
    assert out["created"] == [{"event": "evt_e", "rule": "urn:lla:llaPartnerConversion:99"}]


@pytest.mark.asyncio
async def test_boost_winners_empty_skips_alert(monkeypatch):
    monkeypatch.setattr(LB, "ENABLED", True)
    db = SimpleNamespace(
        execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))),
        commit=AsyncMock(),
    )
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))
    import app.services.slack_notifications as SN

    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)
    out = await LB._boost_linkedin_winners()
    assert out == {"boosted": 0}
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_route_leads_unrouted_skips_alert(monkeypatch):
    monkeypatch.setattr(LB, "ENABLED", True)
    acc = SimpleNamespace(team_id="t1")
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: acc))))
    monkeypatch.setattr(LB, "task_session", lambda: _AsyncCM(db))
    import app.services.linkedin_ads as LADS

    monkeypatch.setattr(LADS, "ad_accounts", AsyncMock(return_value=[{"id": "a1"}]))
    monkeypatch.setattr(LADS, "lead_gen_forms", AsyncMock(return_value=[{"id": "f1"}]))
    monkeypatch.setattr(LADS, "lead_form_responses", AsyncMock(return_value=[{"id": "r1"}]))
    monkeypatch.setattr(LB, "route_lead_response", AsyncMock(return_value=False))
    import app.services.slack_notifications as SN

    alert = AsyncMock()
    monkeypatch.setattr(SN, "post_alert_to_slack", alert)
    out = await LB._route_linkedin_leads()
    assert out == {"routed": 0, "found": 1}
    alert.assert_not_called()


# ── _webhook_selfcheck streak gate ──────────────────────────────────────


class _ProbeResp:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class _ProbeClient:
    def __init__(self, resp=None, exc=None):
        self._resp = resp
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, params=None, **k):
        if self._exc is not None:
            raise self._exc
        if callable(self._resp):
            return self._resp(params or {})
        return self._resp


def _challenge_echo(params):
    return _ProbeResp(200, f'"challengeCode":"{params.get("challengeCode", "")}"')


def _patch_selfcheck(monkeypatch, resp=None, exc=None):
    """Patch the pieces _webhook_selfcheck touches; returns (alerts, streaks)."""
    import httpx

    from app.core.config import settings

    monkeypatch.setattr(settings, "TELEGRAM_WEBHOOK_BASE", "https://api.example.com/api/v1", raising=False)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _ProbeClient(resp=resp, exc=exc))
    state = {"streak": 0}
    streaks: list[int] = []

    async def _streak(ok):
        state["streak"] = 0 if ok else state["streak"] + 1
        streaks.append(state["streak"])
        return state["streak"]

    monkeypatch.setattr(LB, "_selfcheck_streak", _streak)
    monkeypatch.setattr(LB, "_selfcheck_page_fresh", AsyncMock(return_value=True))
    import app.services.slack_notifications as SN

    alert = AsyncMock()
    monkeypatch.setattr(SN, "send_telegram_alert", alert)
    return alert, streaks


@pytest.mark.asyncio
async def test_selfcheck_single_failure_no_alert(monkeypatch):
    """A one-off probe failure (restart window) logs but does not page."""
    alert, _ = _patch_selfcheck(monkeypatch, resp=_ProbeResp(503, "down"))
    await LB._webhook_selfcheck()
    await LB._webhook_selfcheck()
    alert.assert_not_called()


@pytest.mark.asyncio
async def test_selfcheck_third_failure_alerts_with_detail(monkeypatch):
    alert, streaks = _patch_selfcheck(monkeypatch, resp=_ProbeResp(503, "down"))
    for _ in range(3):
        await LB._webhook_selfcheck()
    alert.assert_awaited_once()
    sev, msg = alert.await_args.args
    assert sev == "P2"
    assert "3x in a row" in msg and "HTTP 503" in msg
    assert streaks == [1, 2, 3]


@pytest.mark.asyncio
async def test_selfcheck_alert_respects_cooldown(monkeypatch):
    """A page sent <30min ago suppresses the repeat alert."""
    alert, streaks = _patch_selfcheck(monkeypatch, resp=_ProbeResp(503, "down"))
    monkeypatch.setattr(LB, "_selfcheck_page_fresh", AsyncMock(return_value=False))
    for _ in range(3):
        await LB._webhook_selfcheck()
    alert.assert_not_called()
    assert streaks == [1, 2, 3]


@pytest.mark.asyncio
async def test_selfcheck_success_resets_streak(monkeypatch):
    """Flap recovery clears the streak — next failure starts from 1."""
    alert, streaks = _patch_selfcheck(monkeypatch)
    import httpx

    # fail → fail → succeed → fail must NOT alert (streak restarts at 1)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _ProbeClient(resp=_ProbeResp(503, "down")))
    await LB._webhook_selfcheck()
    await LB._webhook_selfcheck()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _ProbeClient(resp=_challenge_echo))
    await LB._webhook_selfcheck()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _ProbeClient(resp=_ProbeResp(503, "down")))
    await LB._webhook_selfcheck()
    alert.assert_not_called()
    assert streaks == [1, 2, 0, 1]

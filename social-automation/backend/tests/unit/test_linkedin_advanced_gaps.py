"""Coverage for linkedin_advanced lead-sync/conversions/dmp edge paths."""

from unittest.mock import AsyncMock

import pytest

from app.services import linkedin_advanced as ADV


class _Resp:
    def __init__(self, status=200, body=None, headers=None, text=None, bad_json=False):
        self.status_code = status
        self._body = body or {}
        self.headers = headers or {}
        self.text = text if text is not None else str(self._body)
        self._bad_json = bad_json

    def json(self):
        if self._bad_json:
            raise ValueError("no json")
        return self._body


def _patch_ctx(monkeypatch, ctx=("tok", "urn:li:organization:1")):
    monkeypatch.setattr(ADV, "_ctx", AsyncMock(return_value=ctx))


def _patch_http(monkeypatch, responses):
    calls = []

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def _next(self, method, *a, **k):
            calls.append((method, a, k))
            return responses.pop(0)

        async def get(self, *a, **k):
            return self._next("get", *a, **k)

        async def post(self, *a, **k):
            return self._next("post", *a, **k)

    monkeypatch.setattr(ADV.httpx, "AsyncClient", _Client)
    return calls


# ── lead_form_responses / get_lead_form / question map ──────────────────


@pytest.mark.asyncio
async def test_lead_form_responses_non200_and_no_ctx(monkeypatch):
    _patch_ctx(monkeypatch)
    _patch_http(monkeypatch, [_Resp(403, text="denied")])
    out = await ADV.lead_form_responses("urn:li:leadGenForm:1")
    assert out["responses"] == [] and out["status"] == 403

    _patch_ctx(monkeypatch, None)
    out = await ADV.lead_form_responses("urn:li:leadGenForm:1")
    assert out["skip"] == "disabled-or-no-account"


@pytest.mark.asyncio
async def test_get_lead_form_paths(monkeypatch):
    _patch_ctx(monkeypatch, None)
    assert await ADV.get_lead_form("urn:li:leadGenForm:5") is None

    _patch_ctx(monkeypatch)
    _patch_http(
        monkeypatch,
        [
            _Resp(200, {"content": {"questions": [{"questionId": 1}]}}),
            _Resp(404, text="gone"),
        ],
    )
    out = await ADV.get_lead_form("urn:li:versionedLeadGenForm:(urn:li:leadGenForm:5,1)")
    assert out["content"]["questions"][0]["questionId"] == 1
    assert await ADV.get_lead_form("urn:li:leadGenForm:9") is None


def test_question_map_skips_missing_qid_and_builds_options():
    form = {
        "content": {
            "questions": [
                {"question": {"localized": {"en_US": "Email?"}}, "name": "email", "predefinedField": "EMAIL"},
                {
                    "questionId": 9,
                    "question": {"localized": {"el_GR": "Ερώτηση"}},
                    "questionDetails": {"multipleChoiceQuestionDetails": {"options": [{"id": "o1", "text": {"localized": {"en_US": "Yes"}}}]}},
                },
            ]
        }
    }
    m = ADV.lead_form_question_map(form)
    assert 9 in m and m[9]["label"] == "Ερώτηση" and m[9]["options"][0]["text"] == "Yes"
    assert ADV.lead_form_question_map(None) == {}


# ── lead notifications ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_lead_notifications_paths(monkeypatch):
    _patch_ctx(monkeypatch, None)
    out = await ADV.list_lead_notifications("urn:li:sponsoredAccount:1")
    assert out["skip"] == "disabled-or-no-account"

    _patch_ctx(monkeypatch)
    _patch_http(
        monkeypatch,
        [
            _Resp(200, {"elements": [{"id": "n1"}]}),
            _Resp(500, text="err"),
        ],
    )
    out = await ADV.list_lead_notifications("urn:li:organization:2")
    assert out["notifications"][0]["id"] == "n1"
    out = await ADV.list_lead_notifications("urn:li:sponsoredAccount:3")
    assert out["status"] == 500


@pytest.mark.asyncio
async def test_create_lead_notification_paths(monkeypatch):
    _patch_ctx(monkeypatch, None)
    out = await ADV.create_lead_notification("https://x/hook", "urn:li:organization:1")
    assert out["error"] == "disabled-or-no-account"

    _patch_ctx(monkeypatch)
    _patch_http(
        monkeypatch,
        [
            _Resp(201, {}, {"x-restli-id": "notif-1"}),
            _Resp(400, text="bad"),
        ],
    )
    out = await ADV.create_lead_notification("https://x/hook", "urn:li:sponsoredAccount:9")
    assert out["ok"] is True and out["id"] == "notif-1"
    out = await ADV.create_lead_notification("https://x/hook", "urn:li:organization:9")
    assert out["error"] == 400


# ── stream_conversion payload variants ──────────────────────────────────


@pytest.mark.asyncio
async def test_stream_conversion_variants(monkeypatch):
    _patch_ctx(monkeypatch)
    calls = _patch_http(
        monkeypatch,
        [
            _Resp(201, {"elements": [{"status": "ok"}]}),
            _Resp(200, bad_json=True),
        ],
    )
    out = await ADV.stream_conversion(
        conversion_rule="urn:lla:rule:1",
        happened_at_ms=123,
        email_sha256="abc",
        lead_id="raw-lead-id",
        conversion_value=9.99,
        currency_code="EUR",
    )
    assert out["ok"] is True and out["element"]["status"] == "ok"
    element = calls[0][2]["json"]["elements"][0]
    assert element["user"]["lead"] == "urn:li:leadGenFormResponse:raw-lead-id"
    assert element["conversionValue"] == 9.99 and element["currencyCode"] == "EUR"

    # urn lead + bad-json body + no optional fields
    out = await ADV.stream_conversion(conversion_rule="urn:lla:rule:1", happened_at_ms=1, lead_id="urn:li:leadGenFormResponse:5")
    assert out["ok"] is True and "element" not in out
    element = calls[1][2]["json"]["elements"][0]
    assert element["user"]["lead"] == "urn:li:leadGenFormResponse:5"
    assert "userIds" not in element["user"]


# ── conversion rules list/create/resolve ────────────────────────────────


@pytest.mark.asyncio
async def test_list_conversion_rules_paths(monkeypatch):
    _patch_ctx(monkeypatch, None)
    out = await ADV.list_conversion_rules()
    assert out["skip"] == "disabled-or-no-account"

    _patch_ctx(monkeypatch)
    _patch_http(monkeypatch, [_Resp(200, {"elements": [{"id": "1"}]}), _Resp(502, text="badgw")])
    out = await ADV.list_conversion_rules()
    assert out["rules"][0]["id"] == "1"
    out = await ADV.list_conversion_rules()
    assert out["status"] == 502


@pytest.mark.asyncio
async def test_create_conversion_rule_paths(monkeypatch):
    _patch_ctx(monkeypatch, None)
    out = await ADV.create_conversion_rule("n", "LEAD")
    assert out["error"] == "disabled-or-no-account"

    _patch_ctx(monkeypatch)
    _patch_http(
        monkeypatch,
        [
            _Resp(201, {}, {"x-restli-id": "r1"}),
            _Resp(201, {"id": "r2"}),  # id from body
            _Resp(201, bad_json=True),  # json fails, no header -> no-rule-id
            _Resp(500, text="err"),
        ],
    )
    out = await ADV.create_conversion_rule("n1", "LEAD")
    assert out["rule_urn"] == "urn:lla:llaPartnerConversion:r1"
    out = await ADV.create_conversion_rule("n2", "LEAD")
    assert out["id"] == "r2"
    out = await ADV.create_conversion_rule("n3", "LEAD")
    assert out["error"] == "no-rule-id"
    out = await ADV.create_conversion_rule("n4", "LEAD")
    assert out["error"] == 500


@pytest.mark.asyncio
async def test_resolve_conversion_rule_all_paths(monkeypatch):
    assert await ADV.resolve_conversion_rule("nonexistent_event") is None

    monkeypatch.setenv("LINKEDIN_CONVERSION_RULE_AUDIT_BOOKED", "")
    monkeypatch.delenv("LINKEDIN_CONVERSION_RULE", raising=False)

    class _SS:
        async def get(self, key):
            return "urn:from-secret" if key == "LINKEDIN_CONVERSION_RULE" else None

    monkeypatch.setattr("app.services.secret_store.secret_store", _SS())
    out = await ADV.resolve_conversion_rule("audit_booked")
    assert out == "urn:from-secret"

    class _BadSS:
        async def get(self, key):
            raise RuntimeError("store down")

    monkeypatch.setattr("app.services.secret_store.secret_store", _BadSS())
    monkeypatch.delenv("LINKEDIN_CONVERSION_RULE", raising=False)
    out = await ADV.resolve_conversion_rule("audit_booked")
    assert out is None

    monkeypatch.setenv("LINKEDIN_CONVERSION_RULE", "urn:from-env")
    out = await ADV.resolve_conversion_rule("audit_booked")
    assert out == "urn:from-env"


@pytest.mark.asyncio
async def test_create_lead_notification_no_restli_id(monkeypatch):
    _patch_ctx(monkeypatch)
    _patch_http(monkeypatch, [_Resp(200, {"ok": True})])
    out = await ADV.create_lead_notification("https://x/hook", "urn:li:organization:1")
    assert out["ok"] is True and "id" not in out

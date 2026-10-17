"""XSS regression tests for the public unsubscribe/survey pages.

These pages are unauthenticated and render values that come from outside
the page: the recipient email (imported from CRM/CSV), the campaign's survey
definition (labels, options, ids), campaign name/subject, validation
errors that can echo answer keys, and the token taken from the URL path.
Each must be HTML-escaped for its context.
"""
from __future__ import annotations

import json

import pytest

import app.api.email_campaigns as api
from app.models.email_campaign import CampaignKind, RecipientStatus
from tests.unit.test_email_campaigns import _DB, _campaign, _recipient, _Res
from tests.unit.test_email_campaigns_api import _body_request

EVIL = '"><script>alert(1)</script>'
EVIL_ESC = "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"
EVIL_EMAIL = "x<img src=x onerror=alert(1)>@evil.test"


def _assert_no_raw(body: bytes | str, *needles: str) -> None:
    text = body.decode() if isinstance(body, bytes) else body
    for n in needles:
        assert n not in text, f"unescaped {n!r} reached the page"


class TestUnsubscribeEscaping:
    @pytest.mark.asyncio
    async def test_confirm_escapes_email_and_token(self):
        rec = _recipient(email=EVIL_EMAIL, token=EVIL)
        resp = await api.unsubscribe_confirm(EVIL, _DB([_Res([rec])]))
        _assert_no_raw(resp.body, "<img src=x", "<script>")
        text = resp.body.decode()
        assert "x&lt;img src=x onerror=alert(1)&gt;@evil.test" in text
        # token sits inside a double-quoted attribute: the quote must be escaped
        assert f'action="/u/{EVIL_ESC}"' in text

    @pytest.mark.asyncio
    async def test_already_unsubscribed_escapes_email(self):
        rec = _recipient(email=EVIL_EMAIL, status=RecipientStatus.unsubscribed)
        resp = await api.unsubscribe_confirm("t", _DB([_Res([rec])]))
        _assert_no_raw(resp.body, "<img src=x")
        assert b"Already unsubscribed" in resp.body

    @pytest.mark.asyncio
    async def test_post_escapes_email(self):
        rec = _recipient(email=EVIL_EMAIL)
        resp = await api.unsubscribe_post(rec.token, _DB([_Res([rec]), _Res([])]))
        assert resp.status_code == 200
        _assert_no_raw(resp.body, "<img src=x")
        assert b"<b>x&lt;img" in resp.body  # our own markup still renders


class TestSurveyEscaping:
    Q = [
        {"id": 'q1" onfocus="alert(1)', "label": "<b>Pick</b> " + EVIL, "type": "choice",
         "options": ['a" onclick="x', "<i>b</i>"]},
        {"id": "note", "label": "Tell us & more"},
    ]

    def test_question_input_escapes_every_context(self):
        html = api._question_input(self.Q[0])
        _assert_no_raw(html, "<script>", "<b>Pick", "<i>b</i>", '" onfocus=', '" onclick=')
        assert 'for="q1&quot; onfocus=&quot;alert(1)"' in html
        assert 'value="a&quot; onclick=&quot;x"' in html
        assert "&lt;i&gt;b&lt;/i&gt;" in html
        assert "Tell us &amp; more" in api._question_input(self.Q[1])

    def test_rating_values_unchanged(self):
        html = api._question_input({"id": "r", "label": "Rate", "type": "rating", "min": 1, "max": 2})
        assert 'value="1"' in html and 'value="2"' in html

    @pytest.mark.asyncio
    async def test_form_escapes_campaign_name_subject_and_token(self):
        rec = _recipient(token=EVIL)
        camp = _campaign(kind=CampaignKind.survey, name="<script>n()</script>",
                         subject="Hi <img src=x onerror=s()>", meta_data={"questions": self.Q})
        resp = await api.survey_form(EVIL, _DB([_Res([rec]), _Res([]), _Res([camp])]))
        assert resp.status_code == 200
        _assert_no_raw(resp.body, "<script>", "<img src=x")
        text = resp.body.decode()
        assert "&lt;script&gt;n()&lt;/script&gt;" in text  # <title> and <h2>
        assert f'action="/s/{EVIL_ESC}"' in text

    @pytest.mark.asyncio
    async def test_form_with_empty_subject(self):
        rec = _recipient()
        camp = _campaign(kind=CampaignKind.survey, name="Feedback", subject=None,
                         meta_data={"questions": self.Q[1:]})
        resp = await api.survey_form(rec.token, _DB([_Res([rec]), _Res([]), _Res([camp])]))
        assert resp.status_code == 200 and b"Feedback" in resp.body

    @pytest.mark.asyncio
    async def test_submit_errors_are_escaped(self, monkeypatch):
        async def fake_submit(db, token, answers):
            return None, [f"{k}: unknown question" for k in answers]

        monkeypatch.setattr(api.svc, "submit_survey", fake_submit)
        req = _body_request(json.dumps({"answers": {EVIL: "x"}}).encode(), "application/json")
        resp = await api.survey_submit("tok", req, _DB())
        assert resp.status_code == 422
        _assert_no_raw(resp.body, "<script>")
        assert f"{EVIL_ESC}: unknown question".encode() in resp.body

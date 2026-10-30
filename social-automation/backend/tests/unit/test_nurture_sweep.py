"""Coverage: nurture_sequence.nurture_sweep — scan/skip/send paths."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _lead(meta=None, email="a@x.co", name="Ana Maria"):
    lead = MagicMock()
    lead.meta_data = meta or {}
    lead.email = email
    lead.name = name
    return lead


def _db(leads):
    db = MagicMock()
    res = MagicMock()
    res.scalars.return_value.all.return_value = leads
    db.execute = AsyncMock(return_value=res)
    db.commit = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_sweep_sends(monkeypatch):
    from app.services import nurture_sequence as M

    lead = _lead({"form": "newsletter"})
    send = AsyncMock()
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", send)
    monkeypatch.setattr(M, "_due_step", lambda lead: {"step": 2, "subject": "s", "body": "hi {name}"})
    out = await M.nurture_sweep(_db([lead]), MagicMock())
    assert out == {"scanned": 1, "sent": 1, "skipped": 0}
    assert lead.meta_data["nurture_step"] == 2
    send.assert_awaited_once()
    assert send.call_args.kwargs["text_body"] == "hi Ana"


@pytest.mark.asyncio
async def test_sweep_skips_wrong_form_and_no_step(monkeypatch):
    from app.services import nurture_sequence as M

    wrong = _lead({"form": "contact"})
    nostep = _lead({"form": "free-audit"})
    monkeypatch.setattr(M, "_due_step", lambda lead: None)
    out = await M.nurture_sweep(_db([wrong, nostep]), MagicMock())
    assert out == {"scanned": 1, "sent": 0, "skipped": 1}


@pytest.mark.asyncio
async def test_sweep_send_failure_skips(monkeypatch):
    from app.services import nurture_sequence as M

    lead = _lead({"form": "newsletter"}, name=None)
    import app.services.email_digest as ed

    monkeypatch.setattr(ed, "send_email", AsyncMock(side_effect=RuntimeError("smtp")))
    monkeypatch.setattr(M, "_due_step", lambda lead: {"step": 1, "subject": "s", "body": "hi {name}"})
    out = await M.nurture_sweep(_db([lead]), MagicMock())
    assert out["skipped"] == 1 and out["sent"] == 0


@pytest.mark.asyncio
async def test_sweep_no_email_leads(monkeypatch):
    from app.services import nurture_sequence as M

    out = await M.nurture_sweep(_db([]), MagicMock())
    assert out == {"scanned": 0, "sent": 0, "skipped": 0}

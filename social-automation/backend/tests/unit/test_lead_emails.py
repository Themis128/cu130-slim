"""Real-code tests for the playbook-email Celery task.

Fakes only ``run_async`` (worker loop boundary). The bind/retry ladder
is the production task.
"""

from __future__ import annotations

import pytest
from celery.exceptions import Retry

import app.worker.tasks.lead_emails as LE


def test_send_playbook_email_success(monkeypatch):
    monkeypatch.setattr(LE, "run_async", lambda coro: coro.close())
    assert LE.send_playbook_email.run("ada@cloudless.gr", "Ada") == {"sent": True}


def test_send_playbook_email_retries_on_send_failure(monkeypatch):
    def boom(coro):
        coro.close()
        raise RuntimeError("smtp timeout")

    monkeypatch.setattr(LE, "run_async", boom)

    # Direct .run() is eager: Celery's retry(exc=exc) re-raises the original
    # exception. Stub retry so the bind=True ladder is still the production task.
    retried: list[BaseException] = []

    def fake_retry(exc=None, **_kw):
        retried.append(exc)
        raise Retry(exc=exc)

    monkeypatch.setattr(LE.send_playbook_email, "retry", fake_retry)
    with pytest.raises(Retry):
        LE.send_playbook_email.run("ada@cloudless.gr")
    assert retried and isinstance(retried[0], RuntimeError)

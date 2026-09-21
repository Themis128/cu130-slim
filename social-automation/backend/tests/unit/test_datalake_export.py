"""Unit tests for the datalake export serializers (pure helpers, no DB)."""

from __future__ import annotations

from datetime import UTC, datetime

from app.worker.tasks.datalake_export import (
    _email_domain,
    _email_hash,
    _iso,
)


def test_iso_aware_and_naive():
    aware = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    assert _iso(aware) == "2026-09-20T12:00:00+00:00"
    naive = datetime(2026, 9, 20, 12, 0)
    assert _iso(naive) == "2026-09-20T12:00:00+00:00"
    assert _iso(None) is None


def test_email_hash_is_deterministic_and_normalized():
    a = _email_hash("Alice@Example.com")
    b = _email_hash("  alice@example.COM ")
    assert a == b
    assert len(a) == 16
    assert a != "alice@example.com"  # never raw email
    assert _email_hash(None) is None
    assert _email_hash("") is None


def test_email_domain():
    assert _email_domain("Alice@Example.COM") == "example.com"
    assert _email_domain("not-an-email") is None
    assert _email_domain(None) is None
    assert _email_domain("") is None

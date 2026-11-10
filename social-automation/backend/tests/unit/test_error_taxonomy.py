"""Unit tests for the publish-error taxonomy."""

from __future__ import annotations

from app.services.error_taxonomy import TERMINAL, TRANSIENT, UNKNOWN, classify_publish_error


def test_dead_credentials_are_terminal() -> None:
    for err in (
        "Error validating access token: session has been invalidated because the user changed their password",
        "invalid_grant",
        "HTTP 401 Unauthorized",
        "token has been revoked",
        "Token decrypt failed",
        "account not connected",
    ):
        assert classify_publish_error(err) == TERMINAL, err


def test_deterministic_rejections_are_terminal() -> None:
    for err in (
        "Only photo or video can be accepted as media type. (9004)",
        "Invalid parameter",
        "media_type must be CAROUSEL",
        "identical content already published",
        "Quality gate: SEO score 69/100",
        "does not accept link posts",
        "Unsupported platform",
        "url_ownership_unverified",
        "Instagram media URL preflight failed",
    ):
        assert classify_publish_error(err) == TERMINAL, err


def test_transient_errors_are_transient() -> None:
    for err in (
        "HTTP 429 Too Many Requests",
        "rate limit exceeded",
        "ReadTimeout: timed out",
        "503 Service Unavailable",
        "connection reset by peer",
        "container status is IN_PROGRESS",
    ):
        assert classify_publish_error(err) == TRANSIENT, err


def test_unknown_and_empty() -> None:
    assert classify_publish_error(None) == UNKNOWN
    assert classify_publish_error("") == UNKNOWN
    assert classify_publish_error("something odd happened") == UNKNOWN


def test_transient_wins_over_terminal_overlap() -> None:
    # "rate limit" inside an auth-flavored message still retries — the
    # provider asked us to back off, not to reconnect.
    assert classify_publish_error("401 rate limit exceeded, retry later") == TRANSIENT

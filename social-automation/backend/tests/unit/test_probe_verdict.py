"""Unit tests for the shared tri-state probe verdict helper."""

from types import SimpleNamespace

from app.worker._probe_verdict import verdict_from_response, verdict_from_status


class _Resp:
    def __init__(self, status_code: int, text: str = ""):
        self.status_code = status_code
        self.text = text


def test_status_2xx_is_healthy():
    for code in (200, 201, 204):
        assert verdict_from_status(code) is True


def test_status_reject_set_is_dead():
    for code in (400, 401, 403):
        assert verdict_from_status(code) is False


def test_status_everything_else_inconclusive():
    # 404 deliberately inconclusive — missing route is infra, not dead creds.
    for code in (404, 429, 500, 502, 503):
        assert verdict_from_status(code) is None


def test_response_delegates_to_status():
    assert verdict_from_response(_Resp(200)) is True
    assert verdict_from_response(_Resp(401)) is False
    assert verdict_from_response(_Resp(503)) is None


def test_response_with_login_required_text_still_inconclusive():
    # The helper stays conservative: callers with richer signals (e.g. the
    # IG sidecar's 500+LoginRequired body) layer their own classification.
    assert verdict_from_response(_Resp(500, "LoginRequired")) is None


def test_odd_status_codes_do_not_crash():
    assert verdict_from_status(0) is None
    assert verdict_from_status(999) is None
    # SimpleNamespace doubles without .status_code surface as inconclusive
    # via getattr? No — verdict_from_response requires the attribute; callers
    # pass real httpx.Response objects. Guard against None status anyway.
    assert verdict_from_status(None) is None  # type: ignore[arg-type]
    assert verdict_from_response(SimpleNamespace()) is None  # type: ignore[arg-type]

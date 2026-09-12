from app.models.content import PostStatus
from app.worker.tasks.publishing import _compute_post_rollup


def test_rollup_in_flight_stays_publishing():
    status, partial, reason = _compute_post_rollup(
        target_statuses=["published", "pending"],
        in_flight=True,
        failure_reason=None,
    )
    assert status == PostStatus.PUBLISHING
    assert partial is False
    assert reason is None


def test_rollup_partial_success_does_not_fail_post():
    status, partial, reason = _compute_post_rollup(
        target_statuses=["published", "failed", "skipped"],
        in_flight=False,
        failure_reason="Unsupported platform: whatsapp",
    )
    assert status == PostStatus.PUBLISHED
    assert partial is True
    assert reason is None


def test_rollup_all_failed_fails_post_with_reason():
    status, partial, reason = _compute_post_rollup(
        target_statuses=["failed", "failed"],
        in_flight=False,
        failure_reason="Bad request",
    )
    assert status == PostStatus.FAILED
    assert partial is False
    assert "bad request" in (reason or "").lower()


def test_rollup_all_skipped_fails_post():
    status, partial, reason = _compute_post_rollup(
        target_statuses=["skipped"],
        in_flight=False,
        failure_reason=None,
    )
    assert status == PostStatus.FAILED
    assert partial is False
    assert "skipped" in (reason or "").lower()


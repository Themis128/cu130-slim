"""Unit tests for app.worker.tasks.publish_resurrect — infra-failure revival sweep.

External seams are faked: the DB session, the video-task dispatcher, and the
Slack alert. Selection/classification logic runs for real.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.worker.tasks.publish_resurrect as R
from app.models.content import PostStatus
from app.services.error_taxonomy import TERMINAL


def _drive(coro):
    return asyncio.run(coro)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows, scalar=None):
        self._rows = rows
        self._scalar = scalar

    def scalars(self):
        return _Rows(self._rows)

    def all(self):
        return self._rows

    def scalar_one(self):
        return self._scalar if self._scalar is not None else 0


class _DB:
    """execute() returns canned results in call order; get() uses a post map."""

    def __init__(self, results=None, posts=None):
        self._q = list(results or [])
        self._posts = posts or {}
        self.commits = 0
        self.added = []

    async def execute(self, _stmt):
        return self._q.pop(0) if self._q else _Result([])

    async def get(self, _model, key):
        return self._posts.get(key)

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.commits += 1


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


def _target(post_id, error="401 Unauthorized", error_class=TERMINAL, account_id=None):
    return SimpleNamespace(
        post_id=post_id,
        status="failed",
        error_message=error,
        error_class=error_class,
        social_account_id=account_id or uuid.uuid4(),
    )


def _post(pid=None, status=PostStatus.FAILED, meta=None, content="Video post copy.", reason="video render failed"):
    return SimpleNamespace(
        id=pid or uuid.uuid4(),
        status=status,
        meta_data=meta if meta is not None else {},
        content_text=content,
        failure_reason=reason,
        team_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
    )


def _wire(monkeypatch, db, *, delay=None, alert=None):
    monkeypatch.setattr(R, "run_async", _drive)
    monkeypatch.setattr(R, "task_session", lambda: _Session(db))
    import app.services.slack_notifications as sn

    monkeypatch.setattr(sn, "post_alert_to_slack", alert or AsyncMock())
    import app.worker.tasks.media as media

    task = Mock()
    task.delay = delay or Mock(return_value=SimpleNamespace(id="celery-task-1"))
    monkeypatch.setattr(media, "generate_video_asset_task", task)
    return task


# ── pure helpers ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error,expected",
    [
        ("Instagram API error 400: session has been invalidated", True),
        ("401 Unauthorized", True),
        ("invalid_grant: token expired", True),
        ("login failed for sidecar", True),
        ("reauth required for account", True),
        ("Duplicate content: matches published post", False),
        ("quality gate: seo_score 40/100", False),
        ("duplicate media already published", False),
        ("", False),
        (None, False),
    ],
)
def test_is_auth_failure(error, expected):
    assert R._is_auth_failure(error) is expected


# ── sweep: targets ──────────────────────────────────────────────────────


def test_sweep_revives_auth_failed_target(monkeypatch):
    pid = uuid.uuid4()
    acct = uuid.uuid4()
    post = _post(pid=pid, status=PostStatus.PUBLISHED)
    tgt = _target(pid, account_id=acct)
    db = _DB(
        [
            _Result([tgt]),          # failed targets
            _Result([], scalar=0),   # active queue rows
            _Result([], scalar=1),   # lifetime queue rows (<4)
            _Result([]),             # failed posts (none)
        ],
        posts={pid: post},
    )
    alert = AsyncMock()
    _wire(monkeypatch, db, alert=alert)

    out = R.run_publish_resurrect_sweep()

    assert out["targets_revived"] == 1
    assert out["posts_revived"] == 0
    assert tgt.status == "pending"
    assert tgt.error_message is None
    assert len(db.added) == 1
    assert db.added[0].post_id == pid
    assert db.added[0].social_account_id == acct
    assert db.commits == 1
    alert.assert_awaited_once()


def test_sweep_revives_auth_class_on_non_terminal(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, status=PostStatus.PUBLISHED)
    tgt = _target(pid, error="sidecar session expired, please reauth", error_class="transient")
    db = _DB(
        [
            _Result([tgt]),
            _Result([], scalar=0),
            _Result([], scalar=0),
            _Result([]),
        ],
        posts={pid: post},
    )
    _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["targets_revived"] == 1


def test_sweep_skips_content_terminal_target(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, status=PostStatus.PUBLISHED)
    tgt = _target(pid, error="duplicate content: identical media already published")
    db = _DB(
        [
            _Result([tgt]),
            _Result([]),
        ],
        posts={pid: post},
    )
    _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["targets_revived"] == 0
    assert db.added == []


def test_sweep_skips_non_auth_terminal_target(monkeypatch):
    pid = uuid.uuid4()
    tgt = _target(pid, error="media semantic QA failed — image does not match")
    db = _DB([_Result([tgt]), _Result([])])
    _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["targets_revived"] == 0


def test_sweep_skips_target_with_active_queue_row(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, status=PostStatus.PUBLISHED)
    tgt = _target(pid)
    db = _DB(
        [
            _Result([tgt]),
            _Result([], scalar=1),   # active pending/processing row exists
            _Result([]),
        ],
        posts={pid: post},
    )
    _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["targets_revived"] == 0
    assert db.added == []


def test_sweep_skips_target_over_queue_row_budget(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, status=PostStatus.PUBLISHED)
    tgt = _target(pid)
    db = _DB(
        [
            _Result([tgt]),
            _Result([], scalar=0),
            _Result([], scalar=R._MAX_QUEUE_ROWS),
            _Result([]),
        ],
        posts={pid: post},
    )
    _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["targets_revived"] == 0
    assert db.added == []


def test_sweep_skips_target_when_post_missing_or_archived(monkeypatch):
    pid = uuid.uuid4()
    tgt = _target(pid)
    archived = _post(pid=pid, status=PostStatus.ARCHIVED)
    db = _DB(
        [
            _Result([tgt]),
            _Result([], scalar=0),
            _Result([], scalar=0),
            _Result([]),
        ],
        posts={pid: archived},
    )
    _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["targets_revived"] == 0
    assert db.added == []


def test_sweep_skips_target_when_post_absent(monkeypatch):
    pid = uuid.uuid4()
    tgt = _target(pid)
    db = _DB(
        [
            _Result([tgt]),
            _Result([], scalar=0),
            _Result([], scalar=0),
            _Result([]),
        ],
        posts={},
    )
    _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["targets_revived"] == 0


# ── sweep: video-render posts ───────────────────────────────────────────


def test_sweep_revives_failed_video_post(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, meta={"instruction": "a sunrise timelapse over Athens"})
    db = _DB(
        [
            _Result([]),   # failed targets
            _Result([post]),  # failed posts
        ]
    )
    task = _wire(monkeypatch, db)
    alert = AsyncMock()
    import app.services.slack_notifications as sn

    monkeypatch.setattr(sn, "post_alert_to_slack", alert)

    out = R.run_publish_resurrect_sweep()

    assert out["posts_revived"] == 1
    task.delay.assert_called_once()
    args = task.delay.call_args[0]
    assert args[2] == "a sunrise timelapse over Athens"
    assert post.status == PostStatus.DRAFT
    assert post.failure_reason is None
    assert post.meta_data["pending_video_task"] == "celery-task-1"
    assert post.meta_data["resurrect_count"] == 1
    assert post.meta_data["video_render_attempts"] == 0
    alert.assert_awaited_once()


def test_sweep_skips_post_with_pending_task_or_cap(monkeypatch):
    pid1, pid2 = uuid.uuid4(), uuid.uuid4()
    p1 = _post(pid=pid1, meta={"pending_video_task": "already-running"})
    p2 = _post(pid=pid2, meta={"resurrect_count": R._MAX_RESURRECTIONS})
    db = _DB([_Result([]), _Result([p1, p2])])
    task = _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["posts_revived"] == 0
    task.delay.assert_not_called()


def test_sweep_skips_post_without_prompt_or_render_reason(monkeypatch):
    pid1 = uuid.uuid4()
    noprompt = _post(pid=pid1, meta={}, content="", reason="video render failed")
    noprompt.meta_data = {}
    noprompt.content_text = ""
    pid2 = uuid.uuid4()
    notrender = _post(pid=pid2, reason="duplicate content rejected")
    db = _DB([_Result([]), _Result([noprompt, notrender])])
    task = _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["posts_revived"] == 0
    task.delay.assert_not_called()


def test_sweep_dispatch_failure_continues(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid)
    db = _DB([_Result([]), _Result([post])])
    _wire(monkeypatch, db, delay=Mock(side_effect=RuntimeError("broker down")))

    out = R.run_publish_resurrect_sweep()
    assert out["posts_revived"] == 0
    assert post.status == PostStatus.FAILED


def test_sweep_post_uses_instruction_fallback_to_content(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, meta={"instruction": ""}, content="fallback copy prompt")
    db = _DB([_Result([]), _Result([post])])
    task = _wire(monkeypatch, db)

    out = R.run_publish_resurrect_sweep()
    assert out["posts_revived"] == 1
    assert task.delay.call_args[0][2] == "fallback copy prompt"


def test_sweep_empty(monkeypatch):
    db = _DB([_Result([]), _Result([])])
    _wire(monkeypatch, db)
    out = R.run_publish_resurrect_sweep()
    assert out == {"targets_revived": 0, "posts_revived": 0}
    assert db.commits == 1


def test_sweep_slack_alert_failure_swallowed(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid)
    db = _DB([_Result([]), _Result([post])])
    alert = AsyncMock(side_effect=RuntimeError("slack down"))
    _wire(monkeypatch, db, alert=alert)
    import app.services.slack_notifications as sn

    monkeypatch.setattr(sn, "post_alert_to_slack", alert)

    out = R.run_publish_resurrect_sweep()
    assert out["posts_revived"] == 1

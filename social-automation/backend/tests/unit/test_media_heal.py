"""Unit tests for app.worker.tasks.media_heal — the media-failure republish sweep.

External seams are faked: the DB session, branded-slide composer, media
persistence, publish dispatch, Slack alert, and D1 sync. The selection and
requeue logic runs for real.
"""

import asyncio
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import app.worker.tasks.media_heal as M
from app.models.content import PostStatus


def _drive(coro):
    return asyncio.run(coro)


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Rows(self._rows)

    def all(self):
        return self._rows


class _DB:
    """execute() returns canned results in call order; records commits."""

    def __init__(self, results=None):
        self._q = list(results or [])
        self.commits = 0

    async def execute(self, _stmt):
        return self._q.pop(0) if self._q else _Result([])

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks = getattr(self, "rollbacks", 0) + 1

    async def refresh(self, _obj):
        pass


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class _Img:
    def save(self, buf, format=None, **kwargs):
        buf.write(b"JPEGDATA")


def _post(pid=None, status=PostStatus.FAILED, meta=None, content="Grow fast. We handle cloud."):
    return SimpleNamespace(
        id=pid or uuid.uuid4(),
        status=status,
        meta_data=meta if meta is not None else {},
        content_text=content,
        team_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        media_ids=[],
    )


def _target(post_id, status="skipped", error="Media semantic QA failed — image does not match the post copy", account_id=None):
    return SimpleNamespace(
        post_id=post_id,
        status=status,
        error_message=error,
        social_account_id=account_id or uuid.uuid4(),
    )


def _wire(monkeypatch, db, *, publish=None, alert=None, bg=None):
    monkeypatch.setattr(M, "run_async", _drive)
    monkeypatch.setattr(M, "task_session", lambda: _Session(db))
    monkeypatch.setattr(M, "compose_branded_slide", Mock(return_value=_Img()))
    monkeypatch.setattr(M, "_cf_generate_background", AsyncMock(return_value=bg if bg is not None else object()))
    monkeypatch.setattr(M, "_qa_generated_background", AsyncMock(side_effect=lambda img, *a, **k: img))
    monkeypatch.setattr(
        M,
        "persist_generated_image",
        AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4())),
    )
    monkeypatch.setattr(M, "publish_post_now", publish or Mock(return_value={"success": True, "results": []}))
    monkeypatch.setattr(M, "post_alert_to_slack", alert or AsyncMock())
    import app.services.db_sync as db_sync

    monkeypatch.setattr(db_sync, "sync_after_worker_task", AsyncMock(return_value={}))


# ── pure helpers ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "error,expected",
    [
        ("Media semantic QA failed — image does not match the post copy", True),
        ("Post media incomplete: only 0/1 media assets could be resolved from storage", True),
        ("media assets could be resolved from storage — refusing", True),
        ("missing media for instagram", True),
        ("Skipped: identical media already published to this account within the last 30d", False),
        ("Duplicate content: matches published post d1c282d1", False),
        ("Instagram API error 400: session has been invalidated", False),
        ("Quality gate: seo_score 40/100 below minimum 80", False),
        (None, False),
        ("", False),
    ],
)
def test_is_media_failure(error, expected):
    assert M._is_media_failure(error) is expected


@pytest.mark.parametrize(
    "text,expected_title",
    [
        ("First sentence here. Second part follows.", "First sentence here"),
        ("No punctuation at all in this copy", "No punctuation at all in this copy"),
        ("", ""),
        (None, ""),
    ],
)
def test_split_copy(text, expected_title):
    title, _body = M._split_copy(text)
    assert title == expected_title[:110]


# ── sweep ───────────────────────────────────────────────────────────────


def test_sweep_heals_and_queues(monkeypatch):
    pid = uuid.uuid4()
    acct = uuid.uuid4()
    post = _post(pid=pid)
    tgt = _target(pid, account_id=acct)
    db = _DB(
        [
            _Result([pid]),  # candidate post_ids
            _Result([post]),  # posts
            _Result([tgt]),  # per-post targets (eligibility)
            _Result([(acct, "linkedin")]),  # republish targets (account_id, platform)
        ]
    )
    publish = Mock(return_value={"success": True, "results": []})
    _wire(monkeypatch, db, publish=publish)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 1
    assert out["queued"] == [str(pid)]
    assert out["exhausted"] == []
    publish.assert_called_once_with(str(pid), [str(acct)])
    assert post.media_ids  # new branded asset attached
    assert post.meta_data["media_heal_attempts"] == 1
    assert db.commits == 2  # exhausted-flags commit + per-post heal commit
    # healed media is JPEG — Instagram's content API rejects PNG image_url
    assert M.persist_generated_image.call_args.kwargs["extension"] == ".jpg"


def test_sweep_skips_published_and_dedup(monkeypatch):
    pid_pub = uuid.uuid4()
    pid_dedup = uuid.uuid4()
    post_pub = _post(pid=pid_pub)
    post_dedup = _post(pid=pid_dedup)
    tgt_pub = _target(pid_pub, status="published", error=None)
    tgt_dedup = _target(
        pid_dedup,
        error="Skipped: identical media already published to this account within the last 30d",
    )
    db = _DB(
        [
            _Result([pid_pub, pid_dedup]),
            _Result([post_pub, post_dedup]),
            _Result([tgt_pub]),  # post_pub targets
            _Result([tgt_dedup]),  # post_dedup targets
        ]
    )
    publish = Mock()
    _wire(monkeypatch, db, publish=publish)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 0
    assert out["queued"] == []
    publish.assert_not_called()
    assert post_pub.media_ids == []
    assert post_dedup.media_ids == []


def test_sweep_exhausted_alerts_once(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, meta={"media_heal_attempts": M._MAX_HEAL_ATTEMPTS})
    tgt = _target(pid)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
        ]
    )
    alert = AsyncMock()
    publish = Mock()
    rewrite = AsyncMock(return_value=None)
    monkeypatch.setattr(M, "_rewrite_copy_for_media", rewrite)
    _wire(monkeypatch, db, publish=publish, alert=alert)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 0
    assert out["exhausted"] == [str(pid)]
    assert post.meta_data["media_heal_alerted"] is True
    publish.assert_not_called()
    alert.assert_called_once()

    # Second run: already alerted -> no duplicate Slack alert.
    db._q = [_Result([pid]), _Result([post]), _Result([tgt])]
    out2 = M.run_media_heal_sweep()
    assert out2["exhausted"] == []
    assert alert.call_count == 1


def test_sweep_per_post_failure_isolated(monkeypatch):
    pid_ok = uuid.uuid4()
    pid_bad = uuid.uuid4()
    post_ok = _post(pid=pid_ok)
    post_bad = _post(pid=pid_bad)
    tgt_ok = _target(pid_ok)
    tgt_bad = _target(pid_bad)
    db = _DB(
        [
            _Result([pid_ok, pid_bad]),
            _Result([post_ok, post_bad]),
            _Result([tgt_ok]),  # eligibility: post_ok
            _Result([tgt_bad]),  # eligibility: post_bad
            _Result([(tgt_ok.social_account_id, "linkedin")]),  # republish targets: post_ok
            _Result([(tgt_bad.social_account_id, "linkedin")]),  # republish targets: post_bad
        ]
    )
    monkeypatch.setattr(M, "run_async", _drive)
    monkeypatch.setattr(M, "task_session", lambda: _Session(db))
    monkeypatch.setattr(M, "compose_branded_slide", Mock(return_value=_Img()))

    async def _persist(db_, **kwargs):
        if "bad" in (kwargs.get("prompt") or ""):
            raise RuntimeError("storage down")
        return SimpleNamespace(id=uuid.uuid4())

    # make the bad post's title contain 'bad'
    post_bad.content_text = "bad copy. more text"
    monkeypatch.setattr(M, "persist_generated_image", _persist)
    publish = Mock(return_value={"success": True, "results": []})
    monkeypatch.setattr(M, "publish_post_now", publish)
    monkeypatch.setattr(M, "post_alert_to_slack", AsyncMock())
    import app.services.db_sync as db_sync

    monkeypatch.setattr(db_sync, "sync_after_worker_task", AsyncMock(return_value={}))

    out = M.run_media_heal_sweep()

    assert out["healed"] == 1
    assert out["queued"] == [str(pid_ok)]
    assert post_ok.media_ids
    assert post_bad.media_ids == []


def test_sweep_publish_dispatch_failure_continues(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid)
    tgt = _target(pid)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
            _Result([(tgt.social_account_id, "linkedin")]),
        ]
    )
    publish = Mock(side_effect=RuntimeError("celery down"))
    _wire(monkeypatch, db, publish=publish)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 1
    assert out["queued"] == []


def test_sweep_skips_empty_copy_and_none_meta(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, meta=None, content="")
    post.meta_data = None
    tgt = _target(pid)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
            _Result([(tgt.social_account_id, "linkedin")]),
        ]
    )
    publish = Mock()
    _wire(monkeypatch, db, publish=publish)

    out = M.run_media_heal_sweep()
    assert out["healed"] == 0
    publish.assert_not_called()


def test_sweep_alert_and_sync_failures_swallowed(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, meta={"media_heal_attempts": M._MAX_HEAL_ATTEMPTS})
    tgt = _target(pid)
    db = _DB([_Result([pid]), _Result([post]), _Result([tgt])])
    alert = AsyncMock(side_effect=RuntimeError("slack down"))
    monkeypatch.setattr(M, "_rewrite_copy_for_media", AsyncMock(return_value=None))
    _wire(monkeypatch, db, alert=alert)
    import app.services.db_sync as db_sync

    monkeypatch.setattr(db_sync, "sync_after_worker_task", AsyncMock(side_effect=RuntimeError("d1 down")))

    out = M.run_media_heal_sweep()
    assert out["exhausted"] == [str(pid)]


def test_sweep_final_attempt_uses_on_topic_background(monkeypatch):
    """attempts=1 -> this heal is the last allowed -> FLUX bg path."""
    pid = uuid.uuid4()
    post = _post(pid=pid, meta={"media_heal_attempts": 1})
    tgt = _target(pid)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
            _Result([(tgt.social_account_id, "linkedin")]),
        ]
    )
    bg_obj = object()
    compose = Mock(return_value=_Img())
    _wire(monkeypatch, db)
    monkeypatch.setattr(M, "compose_branded_slide", compose)
    cf_bg = AsyncMock(return_value=bg_obj)
    qa_bg = AsyncMock(side_effect=lambda img, *a, **k: img)
    monkeypatch.setattr(M, "_cf_generate_background", cf_bg)
    monkeypatch.setattr(M, "_qa_generated_background", qa_bg)
    publish = Mock(return_value={"success": True, "results": []})
    monkeypatch.setattr(M, "publish_post_now", publish)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 1
    cf_bg.assert_called_once()
    assert "professional business photography concept" in cf_bg.call_args[0][0]
    # Regression: CF_TXT2IMG_FREE is a str — passing [0] sent "@" and every
    # creative-path txt2img 400'd (live battle test 2026-11-10).
    assert cf_bg.call_args[0][1] == M.CF_TXT2IMG_FREE
    qa_bg.assert_called_once()
    # compose_branded_slide receives the QA'd background as first arg
    assert compose.call_args[0][0] is bg_obj


def test_sweep_final_attempt_bg_failure_falls_back(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, meta={"media_heal_attempts": 1})
    tgt = _target(pid)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
            _Result([(tgt.social_account_id, "linkedin")]),
        ]
    )
    compose = Mock(return_value=_Img())
    _wire(monkeypatch, db)
    monkeypatch.setattr(M, "compose_branded_slide", compose)
    monkeypatch.setattr(M, "_cf_generate_background", AsyncMock(return_value=None))
    monkeypatch.setattr(M, "publish_post_now", Mock(return_value={"success": True, "results": []}))

    out = M.run_media_heal_sweep()
    assert out["healed"] == 1
    assert compose.call_args[0][0] is None  # brand canvas only


def test_sweep_skips_post_with_published_target(monkeypatch):
    """A post that already shipped on one platform keeps its targets —
    the media-failed sibling target alone doesn't qualify it."""
    pid = uuid.uuid4()
    post = _post(pid=pid)
    tgt_bad = _target(pid)
    tgt_pub = _target(pid, status="published", error=None)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt_bad, tgt_pub]),
        ]
    )
    publish = Mock()
    _wire(monkeypatch, db, publish=publish)

    out = M.run_media_heal_sweep()
    assert out["healed"] == 0
    publish.assert_not_called()


def test_sweep_bg_exception_falls_back_to_canvas(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid, meta={"media_heal_attempts": 1})
    tgt = _target(pid)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
            _Result([(tgt.social_account_id, "linkedin")]),
        ]
    )
    compose = Mock(return_value=_Img())
    _wire(monkeypatch, db)
    monkeypatch.setattr(M, "compose_branded_slide", compose)
    monkeypatch.setattr(M, "_cf_generate_background", AsyncMock(side_effect=RuntimeError("cf down")))
    monkeypatch.setattr(M, "publish_post_now", Mock(return_value={"success": True, "results": []}))

    out = M.run_media_heal_sweep()
    assert out["healed"] == 1
    assert compose.call_args[0][0] is None


def test_sweep_first_attempt_skips_bg_generation(monkeypatch):
    pid = uuid.uuid4()
    post = _post(pid=pid)  # attempts=0 -> plain slide, no FLUX call
    tgt = _target(pid)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
            _Result([(tgt.social_account_id, "linkedin")]),
        ]
    )
    cf_bg = AsyncMock(return_value=object())
    _wire(monkeypatch, db)
    monkeypatch.setattr(M, "_cf_generate_background", cf_bg)
    monkeypatch.setattr(M, "publish_post_now", Mock(return_value={"success": True, "results": []}))

    out = M.run_media_heal_sweep()
    assert out["healed"] == 1
    cf_bg.assert_not_called()


def test_sweep_empty_queue(monkeypatch):
    db = _DB([_Result([])])
    _wire(monkeypatch, db)
    out = M.run_media_heal_sweep()
    assert out == {"healed": 0, "queued": [], "exhausted": []}


def test_sweep_run_async_failure(monkeypatch):
    def _boom(coro):
        coro.close()
        raise RuntimeError("db gone")

    monkeypatch.setattr(M, "run_async", _boom)
    out = M.run_media_heal_sweep()
    assert out == {"error": "db gone"}


def test_sweep_first_post_failure_does_not_block_second(monkeypatch):
    """Rollback expiry regression: the first post's persist raises, the
    second must still refresh, heal and get queued (ids are logged, not
    expired ORM attributes)."""
    pid_bad = uuid.uuid4()
    pid_ok = uuid.uuid4()
    post_bad = _post(pid=pid_bad, content="bad first. more copy")
    post_ok = _post(pid=pid_ok)
    tgt_bad = _target(pid_bad)
    tgt_ok = _target(pid_ok)
    db = _DB(
        [
            _Result([pid_bad, pid_ok]),
            _Result([post_bad, post_ok]),
            _Result([tgt_bad]),  # eligibility: post_bad
            _Result([tgt_ok]),  # eligibility: post_ok
            _Result([(tgt_bad.social_account_id, "linkedin")]),
            _Result([(tgt_ok.social_account_id, "linkedin")]),
        ]
    )
    monkeypatch.setattr(M, "run_async", _drive)
    monkeypatch.setattr(M, "task_session", lambda: _Session(db))
    monkeypatch.setattr(M, "compose_branded_slide", Mock(return_value=_Img()))

    async def _persist(db_, **kwargs):
        if "bad first" in (kwargs.get("prompt") or ""):
            raise RuntimeError("storage down")
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(M, "persist_generated_image", _persist)
    publish = Mock(return_value={"success": True, "results": []})
    monkeypatch.setattr(M, "publish_post_now", publish)
    monkeypatch.setattr(M, "post_alert_to_slack", AsyncMock())
    import app.services.db_sync as db_sync

    monkeypatch.setattr(db_sync, "sync_after_worker_task", AsyncMock(return_value={}))

    out = M.run_media_heal_sweep()

    assert out["healed"] == 1
    assert out["queued"] == [str(pid_ok)]
    assert post_ok.media_ids
    assert post_bad.media_ids == []


def test_sweep_skips_video_only_targets(monkeypatch):
    """A post whose only republish target is TikTok/YouTube is skipped
    before any media work — a healed image can never satisfy video."""
    pid = uuid.uuid4()
    post = _post(pid=pid)
    tgt = _target(pid)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),  # eligibility
            _Result([(tgt.social_account_id, "tiktok")]),
        ]
    )
    persist = AsyncMock()
    publish = Mock()
    _wire(monkeypatch, db, publish=publish)
    monkeypatch.setattr(M, "persist_generated_image", persist)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 0
    assert out["queued"] == []
    persist.assert_not_called()
    publish.assert_not_called()


def test_sweep_skips_non_single_image_posts(monkeypatch):
    """Carousel/multi-asset or video posts keep their own media — the heal
    only applies to single-image posts."""
    pid = uuid.uuid4()
    post = _post(pid=pid)
    post.media_ids = [uuid.uuid4(), uuid.uuid4()]
    tgt = _target(pid)
    asset = SimpleNamespace(mime_type="image/png")
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),  # eligibility targets
            _Result([asset, asset]),  # media assets: 2 -> not single-image
        ]
    )
    publish = Mock()
    _wire(monkeypatch, db, publish=publish)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 0
    assert post.media_ids == [post.media_ids[0], post.media_ids[1]]
    publish.assert_not_called()


# ── bounded copy-rewrite tier (semantic-QA reject class) ────────────────


def test_sweep_exhausted_rewrite_grants_one_more_heal(monkeypatch):
    """Exhausted media attempts -> one LLM copy rewrite -> one more heal
    cycle on the creative path with the new copy."""
    pid = uuid.uuid4()
    acct = uuid.uuid4()
    post = _post(
        pid=pid,
        meta={"media_heal_attempts": M._MAX_HEAL_ATTEMPTS},
        content="Facebook marketing tips for two platforms.",
    )
    tgt = _target(pid, account_id=acct)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
            _Result([(acct, "facebook")]),  # republish targets in heal loop
        ]
    )
    rewrite = AsyncMock(return_value="Cloud cost engineering that actually ships.")
    monkeypatch.setattr(M, "_rewrite_copy_for_media", rewrite)
    publish = Mock(return_value={"success": True, "results": []})
    _wire(monkeypatch, db, publish=publish)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 1
    rewrite.assert_called_once()
    assert post.meta_data["media_heal_original_copy"].startswith("Facebook marketing")
    assert post.meta_data["media_heal_rewrite_done"] is True
    # reset to MAX-1 then the heal incremented -> back at MAX
    assert post.meta_data["media_heal_attempts"] == M._MAX_HEAL_ATTEMPTS
    assert post.content_text.startswith("Cloud cost")
    publish.assert_called_once_with(str(pid), [str(acct)])


def test_sweep_exhausted_rewrite_done_is_terminal(monkeypatch):
    """rewrite_done already set + attempts still exhausted -> alert, no
    second rewrite, no heal."""
    pid = uuid.uuid4()
    post = _post(
        pid=pid,
        meta={
            "media_heal_attempts": M._MAX_HEAL_ATTEMPTS,
            "media_heal_rewrite_done": True,
        },
    )
    tgt = _target(pid)
    db = _DB([_Result([pid]), _Result([post]), _Result([tgt])])
    rewrite = AsyncMock(return_value="should not be used")
    monkeypatch.setattr(M, "_rewrite_copy_for_media", rewrite)
    alert = AsyncMock()
    publish = Mock()
    _wire(monkeypatch, db, publish=publish, alert=alert)

    out = M.run_media_heal_sweep()

    assert out["exhausted"] == [str(pid)]
    rewrite.assert_not_called()
    publish.assert_not_called()
    alert.assert_called_once()


def test_sweep_exhausted_rewrite_echo_alerts(monkeypatch):
    """Rewrite returns the original text (guard) -> treated as unavailable,
    post alerts instead of looping."""
    pid = uuid.uuid4()
    post = _post(pid=pid, meta={"media_heal_attempts": M._MAX_HEAL_ATTEMPTS})
    tgt = _target(pid)
    db = _DB([_Result([pid]), _Result([post]), _Result([tgt])])
    monkeypatch.setattr(M, "_rewrite_copy_for_media", AsyncMock(return_value=None))
    alert = AsyncMock()
    _wire(monkeypatch, db, alert=alert)

    out = M.run_media_heal_sweep()

    assert out["exhausted"] == [str(pid)]
    assert post.meta_data["media_heal_alerted"] is True
    assert post.meta_data["media_heal_rewrite_failed"] is True
    assert "media_heal_rewrite_done" not in post.meta_data
    alert.assert_called_once()


def test_sweep_exhausted_alerted_only_still_gets_rewrite(monkeypatch):
    """Migration case: posts alerted by pre-rewrite-tier sweeps carry
    media_heal_alerted=True but no rewrite flags — they must still get the
    one bounded rewrite (live post 646a0a39, 2026-11-11)."""
    pid = uuid.uuid4()
    acct = uuid.uuid4()
    post = _post(
        pid=pid,
        meta={
            "media_heal_attempts": M._MAX_HEAL_ATTEMPTS,
            "media_heal_alerted": True,
        },
        content="Facebook marketing tips for two platforms.",
    )
    tgt = _target(pid, account_id=acct)
    db = _DB(
        [
            _Result([pid]),
            _Result([post]),
            _Result([tgt]),
            _Result([(acct, "facebook")]),
        ]
    )
    rewrite = AsyncMock(return_value="Cloud cost engineering that actually ships.")
    monkeypatch.setattr(M, "_rewrite_copy_for_media", rewrite)
    alert = AsyncMock()
    _wire(monkeypatch, db, alert=alert)

    out = M.run_media_heal_sweep()

    assert out["healed"] == 1
    rewrite.assert_called_once()
    assert post.meta_data["media_heal_rewrite_done"] is True
    alert.assert_not_called()  # already alerted historically — no re-alert


# ── _rewrite_copy_for_media unit guards ────────────────────────────────


def test_rewrite_copy_short_original_skips_dmr(monkeypatch):
    import app.services.dmr as dmr

    chat = AsyncMock()
    monkeypatch.setattr(dmr, "call_dmr_chat", chat)
    post = _post(content="too short")
    assert _drive(M._rewrite_copy_for_media(post)) is None
    chat.assert_not_called()


def test_rewrite_copy_success(monkeypatch):
    import app.services.dmr as dmr

    new = "Cloud infrastructure that actually reduces cost. " * 2
    monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(return_value={"text": new}))
    post = _post(content="Original post copy that is long enough to pass the minimum length guard.")
    assert _drive(M._rewrite_copy_for_media(post)) == new.strip()


def test_rewrite_copy_dmr_failure_returns_none(monkeypatch):
    import app.services.dmr as dmr

    monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(side_effect=RuntimeError("dmr down")))
    post = _post(content="Original post copy that is long enough to pass the minimum length guard.")
    assert _drive(M._rewrite_copy_for_media(post)) is None


def test_rewrite_copy_echo_rejected(monkeypatch):
    import app.services.dmr as dmr

    original = "Original post copy that is long enough to pass the minimum length guard."
    monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(return_value={"text": original}))
    post = _post(content=original)
    assert _drive(M._rewrite_copy_for_media(post)) is None


def test_rewrite_copy_empty_result_rejected(monkeypatch):
    import app.services.dmr as dmr

    monkeypatch.setattr(dmr, "call_dmr_chat", AsyncMock(return_value={"text": ""}))
    post = _post(content="Original post copy that is long enough to pass the minimum length guard.")
    assert _drive(M._rewrite_copy_for_media(post)) is None

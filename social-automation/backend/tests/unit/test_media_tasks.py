"""Coverage for app/worker/tasks/media.py."""

import asyncio
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.media as M


def _drive(coro):
    return asyncio.run(coro)


class _Session:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *a):
        return False


class _Lock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def _db(asset=None):
    return SimpleNamespace(
        get=AsyncMock(return_value=asset),
        commit=AsyncMock(),
    )


def _wire_video(monkeypatch, *, db=None, qa=None):
    """Patch every external seam of generate_video_asset_task."""
    monkeypatch.setattr(M, "run_async", _drive)
    monkeypatch.setattr(M.media_ai, "expand_visual_prompt", AsyncMock(return_value="expanded scene"))
    monkeypatch.setattr(M.media_ai, "_load_image_bytes", AsyncMock(return_value=b"img"))
    monkeypatch.setattr(M.media_ai, "verify_media_semantics", qa or AsyncMock(return_value={"match": True, "caption": "c", "reason": "ok"}))
    monkeypatch.setattr(M, "task_session", lambda: _Session(db or _db()))
    monkeypatch.setattr(M.gpu_arbiter, "media_gpu_lock", lambda *a, **k: _Lock())
    gen = AsyncMock(return_value=(b"mp4data", {"filename": "v.mp4", "model": "wan22", "width": 704, "height": 1280, "duration_seconds": 3.4}))
    monkeypatch.setattr(M.comfyui_video, "generate_video", gen)
    segs = AsyncMock(return_value=(b"segdata", {"filename": "s.mp4", "model": "ltx", "width": 704, "height": 1280, "duration_seconds": 9.0}))
    monkeypatch.setattr(M.comfyui_video, "generate_video_segments", segs)
    asset = SimpleNamespace(id=uuid.uuid4(), source=None, generation_prompt=None, duration_seconds=None, meta_data={})
    monkeypatch.setattr(M, "save_uploaded_media", AsyncMock(return_value=asset))

    async def _sync(tables):
        return None

    monkeypatch.setattr(M, "sync_after_worker_task", _sync)

    # ffmpeg: create the expected output frame and succeed
    async def _tt(func, *args, **kw):
        Path(args[0][-1]).write_bytes(b"png")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(asyncio, "to_thread", _tt)
    return gen, segs, asset


_TEAM = str(uuid.uuid4())
_USER = str(uuid.uuid4())


class TestAutoTag:
    def test_invalid_id(self):
        M.auto_tag_asset_task("not-a-uuid")  # warns + returns

    def test_happy(self, monkeypatch):
        tag = AsyncMock()
        monkeypatch.setattr(M.media_ai, "auto_tag_asset", tag)
        monkeypatch.setattr(M, "get_session_factory", lambda: "factory")
        synced = []
        monkeypatch.setattr(M, "sync_after_worker_task", lambda t: synced.append(t) or _noop())
        monkeypatch.setattr(M, "run_async", _drive)
        aid = str(uuid.uuid4())
        M.auto_tag_asset_task(aid)
        tag.assert_awaited_once()
        assert tag.await_args.kwargs["session_factory"] == "factory"
        assert synced == [["media_assets"]]

    def test_exception_swallowed(self, monkeypatch):
        monkeypatch.setattr(M.media_ai, "auto_tag_asset", AsyncMock(side_effect=RuntimeError("x")))
        monkeypatch.setattr(M, "get_session_factory", lambda: 1)
        monkeypatch.setattr(M, "run_async", _drive)
        M.auto_tag_asset_task(str(uuid.uuid4()))  # warns, no raise


async def _noop():
    return None


class TestGenerateVideo:
    def test_basic(self, monkeypatch):
        gen, segs, asset = _wire_video(monkeypatch)
        out = M.generate_video_asset_task(_TEAM, _USER, "a dog")
        assert out == str(asset.id)
        gen.assert_awaited_once()
        segs.assert_not_awaited()
        assert asset.source == "comfyui-wan22"
        assert asset.generation_prompt == "a dog"
        assert asset.duration_seconds == 3
        assert asset.meta_data["semantic_qa"]["match"] is True
        assert asset.meta_data["expanded_prompt"] == "expanded scene"

    def test_scene_prompts_use_segments(self, monkeypatch):
        gen, segs, _ = _wire_video(monkeypatch)
        M.generate_video_asset_task(_TEAM, _USER, "p", options={"scene_prompts": ["shot 1", "", "shot 3"], "model": "ltx"})
        segs.assert_awaited_once()
        prompts = segs.await_args.kwargs["prompts"]
        # blank prompt replaced by expanded prompt
        assert prompts == ["shot 1", "expanded scene", "shot 3"]

    def test_duration_computes_segments(self, monkeypatch):
        gen, segs, _ = _wire_video(monkeypatch)
        M.generate_video_asset_task(_TEAM, _USER, "p", options={"duration_seconds": 6, "num_frames": 48, "frame_rate": 24})
        segs.assert_awaited_once()
        # 48/24 = 2s per segment → 3 segments
        assert len(segs.await_args.kwargs["prompts"]) == 3

    def test_image_asset_missing(self, monkeypatch):
        _wire_video(monkeypatch, db=_db(asset=None))
        with pytest.raises(ValueError, match="not found"):
            M.generate_video_asset_task(_TEAM, _USER, "p", options={"image_asset_id": str(uuid.uuid4())})

    def test_image_asset_no_bytes(self, monkeypatch):
        gen, segs, _ = _wire_video(monkeypatch, db=_db(asset=SimpleNamespace(id=uuid.uuid4())))
        M.media_ai._load_image_bytes = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="could not load"):
            M.generate_video_asset_task(_TEAM, _USER, "p", options={"image_asset_id": str(uuid.uuid4())})

    def test_image_asset_ok(self, monkeypatch):
        gen, segs, _ = _wire_video(monkeypatch, db=_db(asset=SimpleNamespace(id=uuid.uuid4())))
        M.generate_video_asset_task(_TEAM, _USER, "p", options={"image_asset_id": str(uuid.uuid4())})
        assert gen.await_args.kwargs["image_bytes"] == b"img"

    def test_qa_mismatch_retries_once(self, monkeypatch):
        qa = AsyncMock(side_effect=[{"match": False, "reason": "wrong subject"}, {"match": True, "caption": "c2", "reason": "ok"}])
        gen, segs, asset = _wire_video(monkeypatch, qa=qa)
        M.generate_video_asset_task(_TEAM, _USER, "a dog")
        assert gen.await_count == 2
        retry_prompt = gen.await_args_list[1].kwargs["prompt"]
        assert "Avoid: wrong subject" in retry_prompt
        assert asset.meta_data["semantic_qa"]["match"] is True

    def test_asset_saved_before_qa(self, monkeypatch):
        """Regression: a finished render must be durable even if QA dies."""
        order = []
        gen, segs, asset = _wire_video(monkeypatch)

        async def _qa_track(*a, **k):
            order.append("qa")
            return {"match": True, "caption": "c", "reason": "ok"}

        async def _save_track(*a, **k):
            order.append("save")
            return asset

        monkeypatch.setattr(M.media_ai, "verify_media_semantics", _qa_track)
        monkeypatch.setattr(M, "save_uploaded_media", _save_track)
        out = M.generate_video_asset_task(_TEAM, _USER, "a dog")
        assert out == str(asset.id)
        assert order[:2] == ["save", "qa"]

    def test_qa_timeout_preserves_asset(self, monkeypatch):
        """A hanging DMR vision call must not consume the task budget."""
        monkeypatch.setattr(M, "_SEMANTIC_QA_TIMEOUT_S", 0.05)

        async def _hang(*a, **k):
            await asyncio.sleep(60)

        qa = AsyncMock(side_effect=_hang)
        gen, segs, asset = _wire_video(monkeypatch, qa=qa)
        out = M.generate_video_asset_task(_TEAM, _USER, "a dog")
        assert out == str(asset.id)
        assert asset.meta_data["semantic_qa"]["reason"] == "qa timeout"
        assert asset.meta_data["semantic_qa"]["match"] is True

    def test_qa_mismatch_retry_skipped_on_budget(self, monkeypatch):
        """No second render when it can't fit the task soft limit."""
        monkeypatch.setattr(M, "_TASK_SOFT_LIMIT_S", 100)
        qa = AsyncMock(return_value={"match": False, "reason": "wrong subject", "vision": "dmr"})
        gen, segs, asset = _wire_video(monkeypatch, qa=qa)
        out = M.generate_video_asset_task(_TEAM, _USER, "a dog")
        assert out == str(asset.id)
        gen.assert_awaited_once()
        assert asset.meta_data["semantic_qa"]["retry"].startswith("skipped")

    def test_qa_retry_supersedes_original(self, monkeypatch):
        """A successful retry returns the new asset; the original is kept
        and marked superseded — never destroyed."""
        qa = AsyncMock(side_effect=[{"match": False, "reason": "wrong subject", "vision": "dmr"}, {"match": True, "caption": "c2", "reason": "ok"}])
        gen, segs, _ = _wire_video(monkeypatch, qa=qa)
        first = SimpleNamespace(id=uuid.uuid4(), source=None, generation_prompt=None, duration_seconds=None, meta_data={})
        second = SimpleNamespace(id=uuid.uuid4(), source=None, generation_prompt=None, duration_seconds=None, meta_data={})
        monkeypatch.setattr(M, "save_uploaded_media", AsyncMock(side_effect=[first, second]))
        out = M.generate_video_asset_task(_TEAM, _USER, "a dog")
        assert out == str(second.id)
        assert first.meta_data["superseded_by"] == str(second.id)
        assert second.meta_data["semantic_qa"]["match"] is True

    def test_ffmpeg_failure_skips_qa(self, monkeypatch):
        gen, segs, asset = _wire_video(monkeypatch)

        async def _tt_fail(func, *args, **kw):
            return SimpleNamespace(returncode=1)

        monkeypatch.setattr(asyncio, "to_thread", _tt_fail)
        M.generate_video_asset_task(_TEAM, _USER, "p")
        M.media_ai.verify_media_semantics.assert_not_awaited()
        assert asset.meta_data["semantic_qa"]["reason"] == "not run"

    def test_qa_exception_swallowed(self, monkeypatch):
        gen, segs, asset = _wire_video(monkeypatch)

        async def _tt_boom(func, *args, **kw):
            raise RuntimeError("ffmpeg missing")

        monkeypatch.setattr(asyncio, "to_thread", _tt_boom)
        M.generate_video_asset_task(_TEAM, _USER, "p")
        assert asset.meta_data["semantic_qa"]["reason"] == "not run"

    def test_redelivery_returns_finished_asset(self, monkeypatch):
        """Worker death after _persist: redelivery returns the asset, no re-render."""
        gen, segs, asset = _wire_video(monkeypatch)
        monkeypatch.setattr(M.comfyui_video, "resume_get", AsyncMock(return_value={"asset_id": "done-1", "done": True}))
        res = M.generate_video_asset_task.apply(args=(_TEAM, _USER, "a dog"), task_id="r-1")
        assert res.result == "done-1"
        gen.assert_not_awaited()

    def test_resume_key_passed_to_render(self, monkeypatch):
        gen, segs, asset = _wire_video(monkeypatch)
        put = AsyncMock()
        monkeypatch.setattr(M.comfyui_video, "resume_get", AsyncMock(return_value=None))
        monkeypatch.setattr(M.comfyui_video, "resume_put", put)
        res = M.generate_video_asset_task.apply(args=(_TEAM, _USER, "a dog"), task_id="r-2")
        assert res.result == str(asset.id)
        assert gen.await_args.kwargs["resume_key"] == "r-2"
        assert put.await_args.args[1] == {"asset_id": str(asset.id), "done": True}

    def test_qa_mismatch_retry_threads_resume_key(self, monkeypatch):
        """QA-retry render must clear the first prompt_id, then re-checkpoint."""
        qa = AsyncMock(
            side_effect=[
                {"match": False, "reason": "wrong subject", "vision": "v"},
                {"match": True, "caption": "c", "reason": "ok"},
            ]
        )
        gen, segs, asset = _wire_video(monkeypatch, qa=qa)
        put = AsyncMock()
        monkeypatch.setattr(M.comfyui_video, "resume_get", AsyncMock(return_value=None))
        monkeypatch.setattr(M.comfyui_video, "resume_put", put)
        res = M.generate_video_asset_task.apply(args=(_TEAM, _USER, "a dog"), task_id="r-9")
        assert res.result == str(asset.id)
        assert gen.await_count == 2
        puts = [c.args[1] for c in put.await_args_list]
        assert {"prompt_id": None} in puts
        assert puts[-1] == {"asset_id": str(asset.id), "done": True}

    def test_qa_no_budget_early_return(self, monkeypatch):
        """Zero QA budget → 'not run: no budget', asset still returns."""
        monkeypatch.setattr(M, "_TASK_SOFT_LIMIT_S", 74)
        gen, segs, asset = _wire_video(monkeypatch)
        out = M.generate_video_asset_task(_TEAM, _USER, "a dog")
        assert out == str(asset.id)
        assert asset.meta_data["semantic_qa"]["reason"] == "not run: no budget"
        assert asset.meta_data["semantic_qa"]["match"] is True

    def test_qa_retry_render_failure_preserves_first_asset(self, monkeypatch):
        """A dead retry render rolls back + annotates, first asset wins."""
        qa = AsyncMock(return_value={"match": False, "reason": "wrong subject", "vision": "v"})
        db = SimpleNamespace(get=AsyncMock(return_value=None), commit=AsyncMock(), rollback=AsyncMock())
        gen, segs, asset = _wire_video(monkeypatch, qa=qa, db=db)
        gen.side_effect = [
            (b"mp4data", {"filename": "v.mp4", "model": "wan22", "width": 704, "height": 1280, "duration_seconds": 3.4}),
            RuntimeError("render died"),
        ]
        res = M.generate_video_asset_task.apply(args=(_TEAM, _USER, "a dog"), task_id="r-10")
        assert res.result == str(asset.id)
        db.rollback.assert_awaited()
        assert asset.meta_data["semantic_qa"]["retry"].startswith("failed")

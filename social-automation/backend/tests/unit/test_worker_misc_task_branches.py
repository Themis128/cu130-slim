"""Remaining branch arms in small worker tasks: media_enhance batch,
TikTok inbox reconcile, video generation QA and the Instagram DM poller.
They reuse each module's main-suite fakes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.worker.tasks.instagram_messenger as IM
import app.worker.tasks.media as MT
import app.worker.tasks.media_enhance as ME
import app.worker.tasks.tiktok_inbox_reconcile as rec
from tests.unit import test_instagram_messenger_worker as im_suite
from tests.unit import test_media_enhance_task as me_suite
from tests.unit import test_media_tasks as mt_suite
from tests.unit import test_tiktok_inbox_reconcile as tt_suite


@pytest.fixture
def me_deps(monkeypatch):
    sync = AsyncMock()
    persist = AsyncMock()
    monkeypatch.setattr(ME, "sync_after_worker_task", sync)
    import app.services.media_storage as ms

    monkeypatch.setattr(ms, "persist_generated_image", persist)
    monkeypatch.setattr(ME, "_load_asset_bytes", AsyncMock(return_value=b"img"))
    return {"sync": sync, "persist": persist}


@pytest.mark.asyncio
async def test_alt_text_empty_leaves_asset_untouched(me_deps, monkeypatch):
    asset = me_suite._asset(alt_text="keep me")
    db = me_suite._DB([asset])
    monkeypatch.setattr(ME, "task_session", lambda: db)
    monkeypatch.setattr(ME.image_enhance, "generate_alt_text", AsyncMock(return_value=""))
    await ME._run_batch([str(asset.id)], "alt_text", {})
    assert asset.alt_text == "keep me"
    assert db.commits == 0
    me_deps["persist"].assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_transform_output_is_not_persisted(me_deps, monkeypatch):
    a1, a2 = me_suite._asset(), me_suite._asset()
    monkeypatch.setattr(ME, "task_session", lambda: me_suite._DB([a1, a2]))
    # first asset: transform yields no bytes; second: no mime type
    results = iter(
        [
            SimpleNamespace(image_bytes=b"", mime_type="image/png"),
            SimpleNamespace(image_bytes=b"ok", mime_type=None),
        ]
    )
    monkeypatch.setattr(ME.image_transform, "compress_image", lambda b, **kw: next(results))
    await ME._run_batch([str(a1.id), str(a2.id)], "compress", {})
    me_deps["persist"].assert_not_awaited()
    me_deps["sync"].assert_awaited_once_with(["media_assets"])


@pytest.mark.asyncio
async def test_tiktok_publish_complete_without_post_row(monkeypatch):
    acc = tt_suite._account()
    t = tt_suite._target(acc, post=None)
    check = AsyncMock(
        return_value={
            "data": {"status": "PUBLISH_COMPLETE", "publicaly_available_post_id": [123]},
        }
    )
    db = tt_suite._DB([tt_suite._Res([t])])
    tt_suite._wire(monkeypatch, db, [t], check=check)
    out = await rec._reconcile_async()
    assert out["published"] == 1
    assert t.platform_post_id == "123"
    assert t.platform_url == "https://www.tiktok.com/@cloudless/video/123"


# ── media.generate_video_asset_task ────────────────────────────────────


def test_video_qa_retry_frame_extract_fails_keeps_first_verdict(monkeypatch):
    """QA mismatch → one regen; if the retry's frame can't be extracted,
    the original (mismatch) verdict is stored rather than a fake pass."""
    import asyncio

    qa = AsyncMock(return_value={"match": False, "reason": "wrong subject"})
    gen, _, asset = mt_suite._wire_video(monkeypatch, qa=qa)
    calls = {"n": 0}

    async def _tt(func, *args, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            from pathlib import Path

            Path(args[0][-1]).write_bytes(b"png")
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=1)  # retry clip: ffmpeg fails

    monkeypatch.setattr(asyncio, "to_thread", _tt)
    MT.generate_video_asset_task(mt_suite._TEAM, mt_suite._USER, "a dog")
    assert gen.await_count == 2
    qa.assert_awaited_once()
    assert asset.meta_data["semantic_qa"] == {"match": False, "reason": "wrong subject"}


def test_video_unexpanded_prompt_not_recorded(monkeypatch):
    _, _, asset = mt_suite._wire_video(monkeypatch)
    monkeypatch.setattr(MT.media_ai, "expand_visual_prompt", AsyncMock(return_value="a dog"))
    MT.generate_video_asset_task(mt_suite._TEAM, mt_suite._USER, "a dog")
    assert "expanded_prompt" not in asset.meta_data
    assert asset.meta_data["semantic_qa"]["match"] is True


# ── instagram_messenger: whitespace-only inbound text ──────────────────


@pytest.mark.asyncio
async def test_instagram_whitespace_inbound_is_ignored(monkeypatch):
    im_suite._wire(monkeypatch)
    acct = im_suite._account({})
    client = im_suite._graph_client(
        convos={"data": [im_suite._convo(embedded=[{"from": {"id": "u1"}, "message": "hello"}])]},
        msgs={
            "data": [
                {"from": {"id": "u1"}, "message": "hello"},
                {"from": {"id": "u1"}, "message": "   "},  # blank after strip → skipped
            ]
        },
    )
    monkeypatch.setattr(IM, "InstagramAPIClient", lambda **k: client)
    seen: dict = {}
    n = await IM._process_account(None, acct, {}, seen, "t", "a", "u")
    assert n == 1
    assert seen["c1"] == "hello"
    client.send_dm.assert_awaited_once_with("u1", "reply")


# ── skool_watch: module diff filtering and non-dict lesson payload ─────


@pytest.mark.asyncio
async def test_skool_module_diff_skips_known_and_unparseable(monkeypatch):
    import json

    import app.worker.tasks.skool_watch as S
    from tests.unit import test_skool_watch as sk_suite

    monkeypatch.setattr(S.asyncio, "sleep", AsyncMock())
    known, new = "a" * 32, "b" * 32
    bridge = sk_suite._Bridge(
        pages={
            'a[href*="md="]': [
                {"t": "Intro", "h": "/c/no-md-here"},  # href without md= → skipped
                {"t": "Old", "h": f"/c?md={known}"},  # already seen → skipped
                {"t": "Week notes", "h": f"/c?md={new}"},  # genuinely new
            ],
            "Leaderboards": [],
        }
    )
    calls = {"n": 0}
    orig_eval = bridge.evaluate

    async def _eval(expr):
        if "location.href" in expr:
            calls["n"] += 1
            if calls["n"] == 1:
                return {"result": json.dumps({"url": S._COURSE_URL, "text": "ok"})}
            return {"result": json.dumps(["not", "a", "dict"])}  # lesson page
        return await orig_eval(expr)

    bridge.evaluate = _eval
    r = sk_suite._Redis()
    r.kv["skool:vec:seeded"] = "1"
    r.sets[S._SEEN_MODULES] = {known}
    alert = AsyncMock()
    sk_suite._wire(monkeypatch, bridge, redis=r, slack_alert=alert)
    draft = AsyncMock()
    monkeypatch.setattr(S, "_create_draft", draft)

    out = await S._watch()
    assert out["new_modules"] == 1
    lesson = r.hashes[f"skool:vec:lesson:{new}"]
    assert lesson["title"] == "Week notes" and lesson["text"] == ""
    assert f"skool:vec:lesson:{known}" not in r.hashes
    assert r.sets[S._SEEN_MODULES] == {known, new}
    draft.assert_not_awaited()  # not a DAY lesson → no draft

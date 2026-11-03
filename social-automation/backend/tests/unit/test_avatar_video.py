"""Unit tests for the avatar story-video pipeline (service + Celery task)."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import avatar_video as AV

# ── render_avatar (FLUX img2img over ComfyUI) ─────────────────────────────


class _Resp:
    def __init__(self, data=None, status_code=200, content=b""):
        self._data = data
        self.status_code = status_code
        self.content = content

    def json(self):
        return self._data

    def raise_for_status(self):
        return None


class _ComfyClient:
    """Fake httpx.AsyncClient: queued /history replies, then /view bytes."""

    def __init__(self, history, views, **kw):
        self.kw = kw
        self._history = list(history)
        self._views = list(views)
        self.posts = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kw):
        self.posts.append((url, json))
        return _Resp({"prompt_id": "p1"})

    async def get(self, url, params=None, **kw):
        if "/history/" in url:
            return _Resp(self._history.pop(0))
        return _Resp(content=self._views.pop(0))


def _patch_comfy(monkeypatch, history, views=()):
    holder = {}

    def factory(**kw):
        holder["client"] = _ComfyClient(history, views, **kw)
        return holder["client"]

    async def fake_upload(client, data, name):
        return "uploaded.png"

    async def no_sleep(_):
        return None

    monkeypatch.setattr(AV.httpx, "AsyncClient", factory)
    monkeypatch.setattr(AV, "upload_image", fake_upload)
    monkeypatch.setattr(AV.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(AV, "get_settings", lambda: SimpleNamespace(COMFYUI_URL="http://comfy:8188/"))
    return holder


def test_flux_graph_wires_image_and_prompt():
    g = AV._flux_img2img_graph(image_name="x.png", prompt="chibi", seed=3, denoise=0.5)
    nodes = g["prompt"]
    assert nodes["4"]["inputs"]["image"] == "x.png"
    assert nodes["5"]["inputs"]["text"] == "chibi"
    assert nodes["9"]["inputs"]["seed"] == 3
    assert nodes["9"]["inputs"]["denoise"] == 0.5


@pytest.mark.asyncio
async def test_render_avatar_success(monkeypatch):
    big = b"P" * 2000
    history = [
        {},  # not queued yet
        {"p1": {"status": {"status_str": "running"}}},
        {
            "p1": {
                "status": {"status_str": "success"},
                "outputs": {
                    "a": {"images": None},
                    "b": {"images": [{"filename": "tiny.png"}, {"filename": "ok.png"}]},
                },
            }
        },
    ]
    holder = _patch_comfy(monkeypatch, history, views=[b"x", big])
    out = await AV.render_avatar(image_bytes=b"src", style_prompt="chibi")
    assert out == big
    client = holder["client"]
    assert client.kw["base_url"] == "http://comfy:8188"
    url, graph = client.posts[0]
    assert url == "http://comfy:8188/prompt"
    assert graph["prompt"]["4"]["inputs"]["image"] == "uploaded.png"


@pytest.mark.asyncio
async def test_render_avatar_error_status(monkeypatch):
    _patch_comfy(monkeypatch, [{"p1": {"status": {"status_str": "error"}}}])
    with pytest.raises(AV.AvatarVideoError, match="img2img failed"):
        await AV.render_avatar(image_bytes=b"src", style_prompt="s")


@pytest.mark.asyncio
async def test_render_avatar_success_without_usable_image_times_out(monkeypatch):
    _patch_comfy(
        monkeypatch,
        [{"p1": {"status": {"status_str": "success"}, "outputs": {}}}],
    )
    loop_times = iter([0.0, 0.0, 999.0])

    class _Loop:
        def time(self):
            return next(loop_times)

    monkeypatch.setattr(AV.asyncio, "get_event_loop", lambda: _Loop())
    with pytest.raises(AV.AvatarVideoError, match="timed out"):
        await AV.render_avatar(image_bytes=b"src", style_prompt="s", timeout_s=10)


# ── speech bubble / ffmpeg assembly ───────────────────────────────────────


@pytest.fixture
def default_font(monkeypatch):
    from PIL import ImageFont

    real_truetype = ImageFont.truetype

    def fake_truetype(font, size=10, *a, **kw):
        # DejaVu may be absent on CI; fall back to Pillow's bundled font
        # (load_default() itself calls truetype() with a BytesIO).
        if font == AV._FONT:
            return ImageFont.load_default(size=size)
        return real_truetype(font, size, *a, **kw)

    monkeypatch.setattr(ImageFont, "truetype", fake_truetype)


def test_speech_bubble_renders_png(tmp_path, default_font):
    out = tmp_path / "b.png"
    AV._speech_bubble(["Hello there", "", "second line"], out)
    from PIL import Image

    im = Image.open(out)
    assert im.mode == "RGBA" and im.size[0] > 60


class _Run:
    """Fake subprocess.run: records cmds, creates the output file (last arg)."""

    def __init__(self, fail_on=None, create=True):
        self.cmds = []
        self.fail_on = fail_on
        self.create = create

    def __call__(self, cmd, **kw):
        self.cmds.append(cmd)
        idx = len(self.cmds) - 1
        if self.fail_on == idx:
            return SimpleNamespace(returncode=1, stderr=b"ffmpeg exploded")
        if self.create:
            Path(cmd[-1]).write_bytes(b"MP4")
        return SimpleNamespace(returncode=0, stderr=b"")


def _scenes():
    return [AV.Scene(clip="talk", line1="Hi", duration=2.0), AV.Scene(clip="sad", line1="Oh", line2="no")]


def test_assemble_success(tmp_path, monkeypatch, default_font):
    run = _Run()
    monkeypatch.setattr(AV.subprocess, "run", run)
    out = tmp_path / "out.mp4"
    AV._assemble([(b"c1", s) for s in _scenes()], 576, 1024, out)
    assert out.read_bytes() == b"MP4"
    assert len(run.cmds) == 3
    assert "concat" in run.cmds[-1]
    assert "2.0" in run.cmds[0]


def test_assemble_scene_failure(tmp_path, monkeypatch, default_font):
    monkeypatch.setattr(AV.subprocess, "run", _Run(fail_on=0))
    with pytest.raises(AV.AvatarVideoError, match="scene 0 render failed"):
        AV._assemble([(b"c1", _scenes()[0])], 576, 1024, tmp_path / "o.mp4")


def test_assemble_concat_failure(tmp_path, monkeypatch, default_font):
    monkeypatch.setattr(AV.subprocess, "run", _Run(fail_on=1))
    with pytest.raises(AV.AvatarVideoError, match="concat failed"):
        AV._assemble([(b"c1", _scenes()[0])], 576, 1024, tmp_path / "o.mp4")


def test_mux_music_without_music_renames(tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"V")
    out = tmp_path / "o.mp4"
    AV._mux_music(video, None, 10.0, out)
    assert out.read_bytes() == b"V" and not video.exists()


def test_mux_music_success(tmp_path, monkeypatch):
    run = _Run()
    monkeypatch.setattr(AV.subprocess, "run", run)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"V")
    out = tmp_path / "o.mp4"
    AV._mux_music(video, b"MP3", 2.0, out)
    assert out.exists()
    # fade-out start never goes negative for short videos
    assert "st=0.0" in " ".join(run.cmds[0])


def test_mux_music_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(AV.subprocess, "run", _Run(fail_on=0))
    video = tmp_path / "v.mp4"
    video.write_bytes(b"V")
    with pytest.raises(AV.AvatarVideoError, match="music mux failed"):
        AV._mux_music(video, b"MP3", 10.0, tmp_path / "o.mp4")


# ── generate_avatar_video orchestration ───────────────────────────────────


@pytest.mark.asyncio
async def test_generate_requires_scenes():
    with pytest.raises(AV.AvatarVideoError, match="scenes required"):
        await AV.generate_avatar_video(avatar_png=b"a")


@pytest.mark.asyncio
async def test_generate_requires_avatar_or_photo():
    with pytest.raises(AV.AvatarVideoError, match="avatar_png or source_photo"):
        await AV.generate_avatar_video(scenes=_scenes())


def _patch_pipeline(monkeypatch):
    calls = {"video": [], "render": 0}

    async def fake_render(*, image_bytes, style_prompt):
        calls["render"] += 1
        return b"AVATAR"

    async def fake_generate_video(**kw):
        calls["video"].append(kw)
        return b"CLIP-" + kw["prompt"][:4].encode(), {}

    def fake_assemble(scenes, w, h, out):
        out.write_bytes(b"NOCAP")

    def fake_mux(video, music, duration, out):
        out.write_bytes(b"FINAL" + (music or b""))

    monkeypatch.setattr(AV, "render_avatar", fake_render)
    monkeypatch.setattr(AV, "generate_video", fake_generate_video)
    monkeypatch.setattr(AV, "_assemble", fake_assemble)
    monkeypatch.setattr(AV, "_mux_music", fake_mux)
    return calls


@pytest.mark.asyncio
async def test_generate_with_provided_avatar(monkeypatch):
    calls = _patch_pipeline(monkeypatch)
    scenes = _scenes() + [AV.Scene(clip="talk", line1="again")]
    video, meta = await AV.generate_avatar_video(avatar_png=b"PNG", scenes=scenes)
    assert video == b"FINAL"
    assert calls["render"] == 0
    # one clip per unique motion, each conditioned on the avatar
    assert len(calls["video"]) == 2
    assert all(c["image_bytes"] == b"PNG" for c in calls["video"])
    assert meta["clips"] == ["sad", "talk"]
    assert meta["scenes"] == 3
    assert meta["duration_seconds"] == 2.0 + 6.0 + 6.0
    assert meta["avatar_source"] == "provided"


@pytest.mark.asyncio
async def test_generate_from_source_photo_with_music(monkeypatch):
    calls = _patch_pipeline(monkeypatch)
    video, meta = await AV.generate_avatar_video(
        source_photo=b"PHOTO",
        scenes=[AV.Scene(clip="wave hello", line1="hey")],
        music_bytes=b"M",
    )
    assert calls["render"] == 1
    assert calls["video"][0]["prompt"] == "wave hello"  # raw motion prompt passthrough
    assert video == b"FINALM"
    assert meta["avatar_source"] == "img2img"


# ── Celery task wrapper ───────────────────────────────────────────────────


def test_task_delegates_to_run(monkeypatch):
    from app.worker import _async
    from app.worker.tasks import avatar_video as T

    captured = {}

    async def fake_run(**kw):
        captured.update(kw)
        return {"ok": True}

    def fake_run_async(coro, timeout=None):
        import asyncio

        return asyncio.run(coro)

    monkeypatch.setattr(T, "_run", fake_run)
    monkeypatch.setattr(_async, "run_async", fake_run_async)
    out = T.generate_avatar_story.run(scenes=[{"clip": "talk"}], music_url="http://m")
    assert out == {"ok": True}
    assert captured["scenes"] == [{"clip": "talk"}]
    assert captured["music_url"] == "http://m"
    assert captured["width"] == 576 and captured["height"] == 1024


class _Result:
    def __init__(self, first=None, one=None):
        self._first = first
        self._one = one

    def scalars(self):
        return SimpleNamespace(first=lambda: self._first)

    def scalar_one_or_none(self):
        return self._one


class _DB:
    def __init__(self, results):
        self._results = list(results)
        self.commits = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, q):
        return self._results.pop(0)

    async def commit(self):
        self.commits += 1


class _MusicClient:
    def __init__(self, status):
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return SimpleNamespace(status_code=self.status, content=b"MUSIC")


def _patch_run_deps(monkeypatch, db, music_status=200):
    import httpx

    from app.services import media_dedup, media_storage, url_safety
    from app.worker import _async

    seen = {}

    async def fake_generate(**kw):
        seen["generate"] = kw
        return b"VIDEO", {"scenes": len(kw["scenes"])}

    async def fake_save(db_, **kw):
        seen["save"] = kw
        return SimpleNamespace(id=uuid.UUID(int=7))

    async def fake_load(row):
        return b"BYTES-" + row.encode()

    monkeypatch.setattr(_async, "task_session", lambda: db)
    monkeypatch.setattr(AV, "generate_avatar_video", fake_generate)
    monkeypatch.setattr(media_storage, "save_uploaded_media", fake_save)
    monkeypatch.setattr(media_dedup, "load_asset_bytes", fake_load)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _MusicClient(music_status))
    # The task now re-validates music_url server-side (SSRF guard); passthrough
    # here keeps the fetch-path test deterministic without real DNS.
    monkeypatch.setattr(url_safety, "validate_public_http_url", lambda u, allow_http=True: u)
    return seen


def _kw(**over):
    base = dict(
        scenes=[{"clip": "talk", "line1": "Hi", "duration": "3"}, {"clip": "sad"}],
        filename="f.mp4",
        alt_text="alt",
        tags="ai-video",
        team_id=None,
        user_id=None,
        avatar_asset_id=None,
        source_photo_asset_id=None,
        music_url=None,
        width=576,
        height=1024,
    )
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_run_resolves_default_user_and_saves(monkeypatch):
    from app.worker.tasks import avatar_video as T

    user = SimpleNamespace(id=uuid.UUID(int=1))
    member = SimpleNamespace(team_id=uuid.UUID(int=2))
    db = _DB([_Result(first=user), _Result(first=member), _Result(one=None)])
    seen = _patch_run_deps(monkeypatch, db)
    out = await T._run(**_kw(avatar_asset_id=str(uuid.uuid4())))
    assert out == {"asset_id": str(uuid.UUID(int=7)), "bytes": 5, "meta": {"scenes": 2}}
    g = seen["generate"]
    assert g["avatar_png"] is None and g["source_photo"] is None and g["music_bytes"] is None
    assert g["scenes"][0].duration == 3.0 and g["scenes"][1].line1 == ""
    assert seen["save"]["team_id"] == member.team_id
    assert seen["save"]["user_id"] == user.id
    assert seen["save"]["mime_type"] == "video/mp4"
    assert db.commits == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status,expected", [(200, b"MUSIC"), (404, None)])
async def test_run_explicit_ids_assets_and_music(monkeypatch, status, expected):
    from app.worker.tasks import avatar_video as T

    db = _DB([_Result(one="photo")])
    seen = _patch_run_deps(monkeypatch, db, music_status=status)
    uid, tid = uuid.uuid4(), uuid.uuid4()
    await T._run(**_kw(user_id=str(uid), team_id=str(tid), source_photo_asset_id=str(uuid.uuid4()), music_url="https://music.example/x.mp3"))
    g = seen["generate"]
    assert g["source_photo"] == b"BYTES-photo"
    assert g["music_bytes"] == expected
    assert seen["save"]["user_id"] == uid and seen["save"]["team_id"] == tid


@pytest.mark.asyncio
async def test_run_unsafe_music_url_skipped(monkeypatch):
    """A music_url that fails SSRF validation is skipped, never fetched —
    the route validates too, but tasks can be enqueued directly."""
    from app.services import url_safety
    from app.worker.tasks import avatar_video as T

    db = _DB([_Result(one="photo")])
    seen = _patch_run_deps(monkeypatch, db)

    def _reject(u, allow_http=True):
        raise url_safety.UnsafeUrlError("not a public URL")

    monkeypatch.setattr(url_safety, "validate_public_http_url", _reject)
    uid, tid = uuid.uuid4(), uuid.uuid4()
    await T._run(**_kw(user_id=str(uid), team_id=str(tid), source_photo_asset_id=str(uuid.uuid4()), music_url="https://evil.internal/x.mp3"))
    assert seen["generate"]["music_bytes"] is None

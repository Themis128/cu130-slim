"""LTX-Video prompt-graph constraints — bad params must fail before queueing."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import app.services.comfyui_video as CV
from app.services.comfyui_video import (
    ComfyUIVideoError,
    build_i2v_prompt,
    build_t2v_prompt,
    build_wan21_prompt,
    build_wan22_prompt,
    generate_video,
    generate_video_segments,
    upload_image,
)


class TestBuildT2VPromptValidation:
    def test_valid_defaults(self):
        g = build_t2v_prompt(prompt="a scene")
        assert g["prompt"]["14"]["inputs"]["width"] == 480

    def test_dimensions_multiple_of_32(self):
        with pytest.raises(ValueError, match="multiples of 32"):
            build_t2v_prompt(prompt="x", width=500)

    def test_dimension_lower_bound(self):
        with pytest.raises(ValueError, match="128..1216"):
            build_t2v_prompt(prompt="x", width=96)

    def test_dimension_upper_bound(self):
        with pytest.raises(ValueError, match="128..1216"):
            build_t2v_prompt(prompt="x", width=4096)
        with pytest.raises(ValueError, match="128..1216"):
            build_t2v_prompt(prompt="x", height=2048)

    def test_frames_8n_plus_1(self):
        with pytest.raises(ValueError, match="8n\\+1"):
            build_t2v_prompt(prompt="x", num_frames=40)

    def test_frames_bounds(self):
        with pytest.raises(ValueError, match="9..257"):
            build_t2v_prompt(prompt="x", num_frames=1)
        with pytest.raises(ValueError, match="9..257"):
            build_t2v_prompt(prompt="x", num_frames=1001)
        g = build_t2v_prompt(prompt="x", num_frames=257)
        assert g["prompt"]["14"]["inputs"]["length"] == 257

    def test_steps_bounds(self):
        with pytest.raises(ValueError, match="1..60"):
            build_t2v_prompt(prompt="x", steps=0)

    def test_frame_rate_bounds(self):
        with pytest.raises(ValueError, match="frame_rate"):
            build_t2v_prompt(prompt="x", frame_rate=0)
        g = build_t2v_prompt(prompt="x", frame_rate=60)
        assert g["prompt"]["6"]["inputs"]["frame_rate"] == 60.0

    def test_edge_dims_allowed(self):
        g = build_t2v_prompt(prompt="x", width=128, height=1216, num_frames=9)
        latent = g["prompt"]["14"]["inputs"]
        assert (latent["width"], latent["height"], latent["length"]) == (128, 1216, 9)




class _Resp:
    def __init__(self, json_body=None, content=b"", status=200):
        self._json = json_body or {}
        self.content = content
        self.status_code = status

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")


class _FakeClient:
    """Routes httpx calls to per-path queues of responses/exceptions."""

    def __init__(self):
        self.posts = []
        self.gets = []
        self.post_responses = {}
        self.get_responses = {}
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        self.closed = True

    async def post(self, url, **kwargs):
        self.posts.append((url, kwargs))
        item = self.post_responses.get(url)
        if isinstance(item, list):
            item = item.pop(0) if item else _Resp()
        else:
            item = item or _Resp()
        if isinstance(item, BaseException):
            raise item
        return item

    async def get(self, url, **kwargs):
        self.gets.append((url, kwargs))
        item = self.get_responses.get(url)
        if isinstance(item, list):
            item = item.pop(0) if item else _Resp()
        else:
            item = item or _Resp()
        if isinstance(item, BaseException):
            raise item
        return item


def _wire(monkeypatch, client, comfy_url="http://comfy:8188"):
    monkeypatch.setattr(CV, "get_settings",
                        lambda: SimpleNamespace(COMFYUI_URL=comfy_url))
    monkeypatch.setattr(CV.httpx, "AsyncClient", lambda **kw: client)
    awake = AsyncMock()
    monkeypatch.setattr(CV.stack_ops, "keepawake", awake)
    return awake


class TestBuilders:
    def test_t2v_graph_structure(self):
        g = build_t2v_prompt(prompt="hello", seed=42)
        nodes = g["prompt"]
        assert nodes["1"]["inputs"]["unet_name"] == CV.LTXV_UNET
        assert nodes["15"]["inputs"]["seed"] == 42
        assert nodes["15"]["inputs"]["sampler_name"] == "euler"
        assert nodes["14"]["class_type"] == "EmptyLTXVLatentVideo"
        assert nodes["6"]["inputs"]["frame_rate"] == 25.0
        assert nodes["12"]["inputs"]["format"] == "video/h264-mp4"

    def test_t2v_custom_negative(self):
        g = build_t2v_prompt(prompt="x", negative_prompt="bad stuff")
        assert g["prompt"]["5"]["inputs"]["text"] == "bad stuff"

    def test_wan21_graph_and_validation(self):
        g = build_wan21_prompt(prompt="cat", seed=7, filename_prefix="p")
        nodes = g["prompt"]
        assert nodes["1"]["inputs"]["unet_name"] == CV.WAN21_UNET
        assert nodes["15"]["inputs"]["seed"] == 7
        assert nodes["15"]["inputs"]["sampler_name"] == "uni_pc"
        assert nodes["14"]["class_type"] == "EmptyHunyuanLatentVideo"
        assert nodes["12"]["inputs"]["filename_prefix"] == "p"
        for kwargs, msg in [
            (dict(width=500), "multiple"),
            (dict(height=2000), "128..1280"),
            (dict(num_frames=50), r"4n\+1"),
            (dict(num_frames=13), "17..129"),
            (dict(steps=0), "steps"),
            (dict(frame_rate=10), "16..60"),
        ]:
            with pytest.raises(ValueError, match=msg):
                build_wan21_prompt(prompt="x", **kwargs)

    def test_wan22_t2v_and_i2v(self):
        g = build_wan22_prompt(prompt="d", seed=9)
        nodes = g["prompt"]
        assert "20" not in nodes  # no LoadImage for pure T2V
        assert nodes["1"]["inputs"]["unet_name"] == CV.WAN22_UNET
        assert nodes["13"]["class_type"] == "VAEDecodeTiled"
        assert nodes["5"]["inputs"]["text"] == CV.WAN22_NEGATIVE
        g2 = build_wan22_prompt(prompt="d", image_name="in.png")
        assert g2["prompt"]["20"]["inputs"]["image"] == "in.png"
        assert g2["prompt"]["14"]["inputs"]["start_image"] == ["20", 0]

    def test_wan22_validation(self):
        for kwargs in [dict(width=500), dict(height=2000), dict(num_frames=50),
                       dict(num_frames=5), dict(steps=0), dict(frame_rate=8)]:
            with pytest.raises(ValueError):
                build_wan22_prompt(prompt="x", **kwargs)

    def test_i2v_prompt(self):
        g = build_i2v_prompt(prompt="move", image_name="a.png",
                             strength=0.8, seed=5)
        nodes = g["prompt"]
        assert nodes["20"]["inputs"]["image"] == "a.png"
        assert nodes["21"]["class_type"] == "LTXVImgToVideoConditionOnly"
        assert nodes["21"]["inputs"]["strength"] == 0.8
        assert nodes["15"]["inputs"]["latent_image"] == ["21", 0]
        with pytest.raises(ValueError, match="image_name"):
            build_i2v_prompt(prompt="x", image_name="  ")


class TestUploadAndCancel:
    @pytest.mark.asyncio
    async def test_upload_image_ok(self):
        c = _FakeClient()
        c.post_responses["/upload/image"] = _Resp({"name": "srv.png"})
        assert await upload_image(c, b"png", "a.png") == "srv.png"
        assert c.posts[0][1]["files"]["image"][0] == "a.png"

    @pytest.mark.asyncio
    async def test_upload_image_no_name(self):
        c = _FakeClient()
        c.post_responses["/upload/image"] = _Resp({"error": "nope"})
        with pytest.raises(ComfyUIVideoError, match="upload failed"):
            await upload_image(c, b"png", "a.png")

    @pytest.mark.asyncio
    async def test_cancel_running_interrupts(self):
        c = _FakeClient()
        c.get_responses["/queue"] = _Resp({"queue_running": [[0, "pid-1"]]})
        await CV._cancel_prompt(c, "pid-1")
        assert ("POST", "/interrupt") == ("POST", c.posts[0][0])

    @pytest.mark.asyncio
    async def test_cancel_queued_deletes(self):
        c = _FakeClient()
        c.get_responses["/queue"] = _Resp({"queue_running": []})
        await CV._cancel_prompt(c, "pid-2")
        assert c.posts[0][0] == "/queue"
        assert c.posts[0][1]["json"]["delete"] == ["pid-2"]

    @pytest.mark.asyncio
    async def test_cancel_swallows_errors(self):
        c = _FakeClient()
        c.get_responses["/queue"] = RuntimeError("down")
        await CV._cancel_prompt(c, "pid-3")  # no raise


class TestGenerateVideo:
    @pytest.mark.asyncio
    async def test_bad_model(self):
        with pytest.raises(ValueError, match="model"):
            await generate_video(prompt="x", model="ltxv")

    def _history_success(self, prompt_id="p1"):
        return _Resp({prompt_id: {
            "status": {"status_str": "success", "messages": []},
            "outputs": {"12": {"gifs": [{
                "filename": "out.mp4", "type": "output",
                "subfolder": "sub"}]}}}})

    @pytest.mark.asyncio
    async def test_happy_path(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp({"prompt_id": "p1"})
        c.get_responses["/history/p1"] = [self._history_success()]
        c.get_responses["/view"] = _Resp(content=b"x" * 2048)
        _wire(monkeypatch, c)
        data, meta = await generate_video(prompt="hi", poll_s=0.01,
                                          timeout_s=30)
        assert data == b"x" * 2048
        assert meta["filename"] == "out.mp4"
        assert meta["model"] == "wan22"
        assert meta["duration_seconds"] == 3.38  # 81/24

    @pytest.mark.asyncio
    async def test_normalization(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp({"prompt_id": "p1"})
        c.get_responses["/history/p1"] = [self._history_success()]
        c.get_responses["/view"] = _Resp(content=b"x" * 2048)
        _wire(monkeypatch, c)
        _, meta = await generate_video(prompt="hi", num_frames=50,
                                       frame_rate=25, steps=25, cfg=3.0,
                                       poll_s=0.01, timeout_s=30)
        graph = c.posts[0][1]["json"]["prompt"]
        assert graph["14"]["inputs"]["length"] == 81   # normalized 4n+1
        assert graph["15"]["inputs"]["steps"] == 20
        assert graph["15"]["inputs"]["cfg"] == 5.0
        assert meta["frame_rate"] == 24

    @pytest.mark.asyncio
    async def test_i2v_uploads_first(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/upload/image"] = _Resp({"name": "srv.png"})
        c.post_responses["/prompt"] = _Resp({"prompt_id": "p1"})
        c.get_responses["/history/p1"] = [self._history_success()]
        c.get_responses["/view"] = _Resp(content=b"x" * 2048)
        _wire(monkeypatch, c)
        await generate_video(prompt="hi", image_bytes=b"img",
                             poll_s=0.01, timeout_s=30)
        graph = c.posts[1][1]["json"]["prompt"]
        assert graph["20"]["inputs"]["image"] == "srv.png"
        assert graph["14"]["inputs"]["start_image"] == ["20", 0]

    @pytest.mark.asyncio
    async def test_node_errors(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp(
            {"prompt_id": "p1", "node_errors": {"15": "bad"}})
        _wire(monkeypatch, c)
        with pytest.raises(ComfyUIVideoError, match="rejected"):
            await generate_video(prompt="x", poll_s=0.01, timeout_s=10)

    @pytest.mark.asyncio
    async def test_no_prompt_id(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp({"something": "else"})
        _wire(monkeypatch, c)
        with pytest.raises(ComfyUIVideoError, match="no prompt_id"):
            await generate_video(prompt="x", poll_s=0.01, timeout_s=10)

    @pytest.mark.asyncio
    async def test_execution_error(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp({"prompt_id": "p1"})
        c.get_responses["/history/p1"] = [_Resp({"p1": {
            "status": {"status_str": "error", "messages": [
                ["execution_error", {"exception_message": "OOM"}]]},
            "outputs": {}}})]
        _wire(monkeypatch, c)
        with pytest.raises(ComfyUIVideoError, match="OOM"):
            await generate_video(prompt="x", poll_s=0.01, timeout_s=10)

    @pytest.mark.asyncio
    async def test_small_video_guard(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp({"prompt_id": "p1"})
        c.get_responses["/history/p1"] = [self._history_success()]
        c.get_responses["/view"] = _Resp(content=b"tiny")
        _wire(monkeypatch, c)
        with pytest.raises(ComfyUIVideoError, match="small"):
            await generate_video(prompt="x", poll_s=0.01, timeout_s=10)

    @pytest.mark.asyncio
    async def test_timeout_cancels(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp({"prompt_id": "p9"})
        c.get_responses["/queue"] = _Resp({"queue_running": []})
        _wire(monkeypatch, c)
        with pytest.raises(ComfyUIVideoError, match="timed out"):
            await generate_video(prompt="x", timeout_s=0.001, poll_s=0.001)
        assert c.posts[-1][0] == "/queue"  # delete issued

    @pytest.mark.asyncio
    async def test_poll_keeps_waiting(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp({"prompt_id": "p1"})
        c.get_responses["/history/p1"] = [
            _Resp({}),  # no entry yet
            _Resp({"other": {}}),  # different prompt
            self._history_success(),
        ]
        c.get_responses["/view"] = _Resp(content=b"x" * 2048)
        _wire(monkeypatch, c)
        data, _ = await generate_video(prompt="x", poll_s=0.01, timeout_s=10)
        assert data == b"x" * 2048
        assert len([g for g in c.gets if "/history/" in g[0]]) == 3

    @pytest.mark.asyncio
    async def test_cancelled_cancels(self, monkeypatch):
        c = _FakeClient()
        c.post_responses["/prompt"] = _Resp({"prompt_id": "p1"})
        c.get_responses["/history/p1"] = asyncio.CancelledError()
        c.get_responses["/queue"] = _Resp({"queue_running": [[0, "p1"]]})
        _wire(monkeypatch, c)
        with pytest.raises(asyncio.CancelledError):
            await generate_video(prompt="x", poll_s=0.01, timeout_s=30)
        assert c.posts[-1][0] == "/interrupt"  # running job interrupted


class TestSegments:
    @pytest.mark.asyncio
    async def test_empty_prompts(self):
        with pytest.raises(ValueError, match="at least one"):
            await generate_video_segments(prompts=[])

    @pytest.mark.asyncio
    async def test_single_segment_passthrough(self, monkeypatch):
        gen = AsyncMock(return_value=(b"mp4", {"prompt_id": "p",
                                               "filename": "f.mp4"}))
        monkeypatch.setattr(CV, "generate_video", gen)
        data, meta = await generate_video_segments(prompts=["one"])
        assert data == b"mp4"
        assert meta["filename"] == "f.mp4"

    @pytest.mark.asyncio
    async def test_multi_segment_concat(self, monkeypatch):
        gen = AsyncMock(side_effect=[
            (b"s1", {"prompt_id": "p1", "filename": "a.mp4"}),
            (b"s2", {"prompt_id": "p2", "filename": "b.mp4"}),
        ])
        monkeypatch.setattr(CV, "generate_video", gen)
        monkeypatch.setattr(CV, "_concat_mp4s", lambda b, fr: b"combined")
        data, meta = await generate_video_segments(
            prompts=["a", "b"], num_frames=41, frame_rate=20)
        assert data == b"combined"
        assert meta["segments"] == 2
        assert meta["num_frames"] == 82
        assert meta["duration_seconds"] == 4.1
        assert gen.await_count == 2


class TestConcatMp4s:
    def test_concat_success(self, monkeypatch):
        def fake_run(cmd, **kw):
            # write the output file the concat command expects
            with open(cmd[-1], "wb") as f:
                f.write(b"joined")
            return SimpleNamespace(returncode=0, stderr=b"")
        monkeypatch.setattr(CV.subprocess, "run", fake_run)
        assert CV._concat_mp4s([b"a", b"b"], 24) == b"joined"

    def test_concat_failure(self, monkeypatch):
        monkeypatch.setattr(
            CV.subprocess, "run",
            lambda cmd, **kw: SimpleNamespace(
                returncode=1, stderr=b"bad ffmpeg"))
        with pytest.raises(ComfyUIVideoError, match="ffmpeg concat failed"):
            CV._concat_mp4s([b"a"], 24)

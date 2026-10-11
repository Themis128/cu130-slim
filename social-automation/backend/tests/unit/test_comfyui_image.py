"""Unit tests for app/services/comfyui_image.py — FLUX.1-schnell txt2img.

The ComfyUI HTTP surface is faked at httpx.AsyncClient; the graph builder,
validation, polling and error mapping are the real code paths.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import app.services.comfyui_image as CI
from app.services.comfyui_image import ComfyUIImageError, build_flux_prompt, generate_image

# ── build_flux_prompt ───────────────────────────────────────────────


def test_graph_structure_and_wiring():
    g = build_flux_prompt(prompt="neon city", width=1024, height=1024,
                          steps=4, seed=7, filename_prefix="p")["prompt"]
    assert g["1"]["inputs"]["unet_name"] == CI.FLUX_UNET
    assert g["2"]["inputs"]["clip_name1"] == CI.FLUX_CLIP_L
    assert g["2"]["inputs"]["clip_name2"] == CI.FLUX_T5
    assert g["3"]["inputs"]["vae_name"] == CI.FLUX_VAE
    assert g["4"]["inputs"]["text"] == "neon city"
    assert g["5"]["inputs"]["text"] == ""        # schnell: cfg 1.0, negative unused
    assert g["7"]["inputs"]["seed"] == 7
    assert g["7"]["inputs"]["cfg"] == 1.0
    assert g["9"]["inputs"]["filename_prefix"] == "p"


def test_graph_random_seed_when_unset():
    g1 = build_flux_prompt(prompt="x")["prompt"]["7"]["inputs"]["seed"]
    g2 = build_flux_prompt(prompt="x")["prompt"]["7"]["inputs"]["seed"]
    assert isinstance(g1, int) and g1 != g2


@pytest.mark.parametrize("kw", [
    {"width": 1000},            # not multiple of 16
    {"width": 128},             # below 256
    {"width": 1504},            # above 1440
    {"steps": 0},
    {"steps": 21},
])
def test_graph_validation_errors(kw):
    with pytest.raises(ValueError):
        build_flux_prompt(prompt="x", **kw)


def test_graph_edge_dims_allowed():
    build_flux_prompt(prompt="x", width=256, height=256)
    build_flux_prompt(prompt="x", width=1440, height=1280)


# ── generate_image ──────────────────────────────────────────────────


class _Resp:
    def __init__(self, status=200, json_body=None, content=b""):
        self.status_code = status
        self._json = json_body
        self.content = content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._json


class _Client:
    """Scripted ComfyUI: post → /prompt, get → /history, /view, /interrupt."""

    def __init__(self, post_resp, hist_resps, view_resp=None):
        self.post_resp = post_resp
        self.hist_resps = list(hist_resps)
        self.view_resp = view_resp
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.calls.append(("POST", url))
        return self.post_resp

    async def get(self, url, params=None, timeout=None):
        self.calls.append(("GET", url))
        if "/history/" in url:
            return self.hist_resps.pop(0) if self.hist_resps else _Resp(200, {})
        if url == "/view":
            return self.view_resp
        return _Resp()


def _patch(monkeypatch, client):
    monkeypatch.setattr(CI, "stack_ops", type("SO", (), {"keepawake": AsyncMock()}))
    monkeypatch.setattr(CI.httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setattr(CI, "_cancel_prompt", AsyncMock())


@pytest.mark.asyncio
async def test_generate_happy(monkeypatch):
    png = b"\x89PNG" + b"x" * 2048
    client = _Client(
        post_resp=_Resp(200, {"prompt_id": "pid-1", "node_errors": {}}),
        hist_resps=[
            _Resp(200, {}),  # first poll: not ready
            _Resp(200, {"pid-1": {
                "status": {"status_str": "success", "messages": []},
                "outputs": {"9": {"images": [{
                    "filename": "out.png", "type": "output", "subfolder": ""}]}}}}),
        ],
        view_resp=_Resp(200, content=png),
    )
    _patch(monkeypatch, client)
    data, meta = await generate_image(prompt="cat", seed=1, poll_s=0.01)
    assert data == png
    assert meta["prompt_id"] == "pid-1" and meta["filename"] == "out.png"
    assert meta["model"] == "flux1-schnell-Q4_0" and meta["provider"] == "comfyui-flux"


@pytest.mark.asyncio
async def test_generate_node_errors_rejected(monkeypatch):
    client = _Client(
        post_resp=_Resp(200, {"prompt_id": None,
                              "node_errors": {"7": {"errors": ["bad"]}}}),
        hist_resps=[],
    )
    _patch(monkeypatch, client)
    with pytest.raises(ComfyUIImageError, match="rejected"):
        await generate_image(prompt="x")


@pytest.mark.asyncio
async def test_generate_no_prompt_id(monkeypatch):
    client = _Client(post_resp=_Resp(200, {}), hist_resps=[])
    _patch(monkeypatch, client)
    with pytest.raises(ComfyUIImageError, match="no prompt_id"):
        await generate_image(prompt="x")


@pytest.mark.asyncio
async def test_generate_execution_error(monkeypatch):
    client = _Client(
        post_resp=_Resp(200, {"prompt_id": "p1", "node_errors": {}}),
        hist_resps=[_Resp(200, {"p1": {
            "status": {"status_str": "error",
                       "messages": [("execution_error", {"exception_message": "OOM"})]},
            "outputs": {}}})],
    )
    _patch(monkeypatch, client)
    with pytest.raises(ComfyUIImageError, match="OOM"):
        await generate_image(prompt="x", poll_s=0.01)


@pytest.mark.asyncio
async def test_generate_timeout_cancels(monkeypatch):
    client = _Client(
        post_resp=_Resp(200, {"prompt_id": "p1", "node_errors": {}}),
        hist_resps=[],  # /history always empty → timeout
    )
    _patch(monkeypatch, client)
    with pytest.raises(ComfyUIImageError, match="timed out"):
        await generate_image(prompt="x", timeout_s=0.05, poll_s=0.01)
    CI._cancel_prompt.assert_awaited()


@pytest.mark.asyncio
async def test_generate_small_image_rejected(monkeypatch):
    client = _Client(
        post_resp=_Resp(200, {"prompt_id": "p1", "node_errors": {}}),
        hist_resps=[_Resp(200, {"p1": {
            "status": {"status_str": "success"},
            "outputs": {"9": {"images": [{"filename": "t.png"}]}}}})],
        view_resp=_Resp(200, content=b"tiny"),
    )
    _patch(monkeypatch, client)
    with pytest.raises(ComfyUIImageError, match="suspiciously small"):
        await generate_image(prompt="x", poll_s=0.01)

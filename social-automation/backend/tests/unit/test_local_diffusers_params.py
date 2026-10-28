"""SD 1.5 parameter normalisation for the local Diffusers provider.

Regression for "strange images": FLUX-style defaults (1024x1024, 4 steps,
cfg 3.5) were sent straight to Stable Diffusion 1.5, producing noise and
tiled/duplicated texture instead of the prompted subject.
"""

import base64
import io

import pytest
from PIL import Image

from app.services import inference

SD15 = "stable-diffusion-v1-5/stable-diffusion-v1-5"


def _png_b64(w: int, h: int) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (10, 200, 200)).save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


class _Resp:
    def __init__(self, body):
        self.status_code = 200
        self._body = body
        self.text = ""

    def json(self):
        return self._body


class _Client:
    def __init__(self, png_size):
        self.png_size = png_size
        self.last_json = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, json=None):
        self.last_json = json
        w, h = (int(x) for x in json["size"].split("x"))
        return _Resp({"data": [{"b64_json": _png_b64(w, h)}]})


@pytest.mark.parametrize(
    ("w", "h", "expected"),
    [
        (1024, 1024, (512, 512)),
        (512, 512, (512, 512)),
        (768, 768, (512, 512)),
        (1080, 1920, (384, 704)),
        (1920, 1080, (704, 384)),
        (256, 256, (256, 256)),
    ],
)
def test_sd15_native_size(w, h, expected):
    assert inference._native_generation_size(w, h, "sd15") == expected


def test_other_family_passthrough():
    assert inference._native_generation_size(1024, 1024, "other") == (1024, 1024)


@pytest.mark.parametrize(
    ("model", "family"),
    [
        (SD15, "sd15"),
        ("stabilityai/stable-diffusion-xl-base-1.0", "sdxl"),
        ("black-forest-labs/FLUX.1-schnell", "other"),
        (None, "other"),
    ],
)
def test_family_detection(model, family):
    assert inference._local_diffusers_family(model) == family


def test_flux_defaults_are_corrected_for_sd15():
    eff = inference._normalize_local_diffusers_params(model=SD15, width=1024, height=1024, steps=4, cfg_scale=3.5, negative_prompt="")
    assert (eff["gen_width"], eff["gen_height"]) == (512, 512)
    assert eff["steps"] == 25
    assert eff["cfg_scale"] == 7.5
    assert "repetitive pattern" in eff["negative_prompt"]


def test_caller_values_respected_when_sane():
    eff = inference._normalize_local_diffusers_params(model=SD15, width=512, height=512, steps=30, cfg_scale=8.0, negative_prompt="text, logo")
    assert eff["steps"] == 30
    assert eff["cfg_scale"] == 8.0
    assert eff["negative_prompt"].startswith("text, logo, ")


@pytest.mark.asyncio
async def test_call_renders_native_and_upscales(monkeypatch):
    client = _Client(None)
    monkeypatch.setattr(inference.httpx, "AsyncClient", lambda timeout=120.0: client)
    monkeypatch.setattr(inference.settings, "LOCAL_DIFFUSERS_URL", "http://local-diffusers:7860")
    monkeypatch.setattr(inference.settings, "LOCAL_DIFFUSERS_MODEL", SD15)

    result = await inference._call_local_diffusers_txt2img("four single-board computers", width=1024, height=1024, steps=4, cfg_scale=3.5)

    sent = client.last_json
    assert sent["size"] == "512x512"
    assert sent["steps"] == 25
    assert sent["guidance_scale"] == 7.5
    assert sent["negative_prompt"]
    img = Image.open(io.BytesIO(base64.b64decode(result["image_base64"])))
    assert img.size == (1024, 1024)
    assert result["params"]["size"] == "512x512"

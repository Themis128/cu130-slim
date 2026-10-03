"""LTX-Video prompt-graph constraints — bad params must fail before queueing."""
import pytest

from app.services.comfyui_video import build_t2v_prompt


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

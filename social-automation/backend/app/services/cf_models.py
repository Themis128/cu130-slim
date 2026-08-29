"""Cloudflare Workers AI model defaults optimized for the free neuron pool.

All Workers AI models share the same daily free allocation (10,000 Neurons).
There is no unlimited "free model" — these IDs are free-*tier eligible* and
low neuron cost so a carousel run is more likely to fit in the daily budget.

Pricing reference: https://developers.cloudflare.com/workers-ai/platform/pricing/
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Text / LLM models
# ---------------------------------------------------------------------------

# High-quality structured-output model (70B fp8 — free tier eligible, fast).
# Best for carousel copy, NLP plain-English rewrite, prompt enhancement.
CF_TEXT_FREE = "@cf/meta/llama-3.3-70b-instruct-fp8-fast"

# Lightweight fallback when 70B is capacity-limited (3B ~ 1/10 the cost).
CF_TEXT_FREE_SMALL = "@cf/meta/llama-3.2-3b-instruct"
# Tiny emergency fallback
CF_TEXT_FREE_TINY = "@cf/meta/llama-3.2-1b-instruct"

# ---------------------------------------------------------------------------
# Image generation models
# ---------------------------------------------------------------------------

# Fast txt2img — FLUX.1-schnell (4-8 steps, free tier)
CF_TXT2IMG_FREE = "@cf/black-forest-labs/flux-1-schnell"

# img2img enhancement — SD v1.5 img2img (free tier; far cheaper than FLUX.2)
CF_IMG2IMG_FREE = "@cf/runwayml/stable-diffusion-v1-5-img2img"

# Avoid as default on free tier (higher neuron cost):
#   @cf/black-forest-labs/flux-2-klein-4b / flux-2-klein-9b
#   @cf/meta/llama-3.1-8b-instruct (non-fp8)
# Paid-only frontier models (403 on free): kimi-k2.6, glm-5.2/5.3, etc.

# ---------------------------------------------------------------------------
# Hugging Face Inference API — free-tier failover
# ---------------------------------------------------------------------------
# Used transparently when CF returns 429/503 (neuron budget exhausted).
# All models below are on HF's free Serverless Inference tier.

HF_API_BASE = "https://api-inference.huggingface.co"

# Text: Mistral-7B-Instruct-v0.3 — OpenAI-compat endpoint, free tier, Apache-2.0
HF_TEXT_FALLBACK = "mistralai/Mistral-7B-Instruct-v0.3"

# txt2img: FLUX.1-schnell — same model as CF_TXT2IMG_FREE, Apache-2.0
HF_TXT2IMG_FALLBACK = "black-forest-labs/FLUX.1-schnell"

# img2img: InstructPix2Pix — text-guided image editing, free tier
HF_IMG2IMG_FALLBACK = "timbrooks/instruct-pix2pix"

# ---------------------------------------------------------------------------
# Carousel pipeline defaults
# ---------------------------------------------------------------------------

CF_CAROUSEL_DEFAULTS: dict[str, str | int] = {
    "text_model": CF_TEXT_FREE,
    "txt2img_model": CF_TXT2IMG_FREE,
    "img2img_model": CF_IMG2IMG_FREE,
    "txt2img_steps": 4,
    "img2img_steps": 8,
}

# ---------------------------------------------------------------------------
# Per-content-type workflow configs (CF free tier + HF fallback)
# ---------------------------------------------------------------------------

CONTENT_WORKFLOW_CONFIGS: dict[str, dict] = {
    "carousel": {
        "content_type": "carousel",
        "name": "Carousel Generator",
        "description": "AI-powered LinkedIn carousel with FLUX images and SD img2img enhancement",
        "text_model": CF_TEXT_FREE,
        "txt2img_model": CF_TXT2IMG_FREE,
        "img2img_model": CF_IMG2IMG_FREE,
        "hf_text_fallback": HF_TEXT_FALLBACK,
        "hf_txt2img_fallback": HF_TXT2IMG_FALLBACK,
        "hf_img2img_fallback": HF_IMG2IMG_FALLBACK,
        "txt2img_steps": 4,
        "img2img_steps": 8,
    },
    "post": {
        "content_type": "post",
        "name": "Social Post Generator",
        "description": "AI-generated posts for LinkedIn, Twitter, Instagram and Facebook",
        "text_model": CF_TEXT_FREE,
        "hf_text_fallback": HF_TEXT_FALLBACK,
    },
    "thread": {
        "content_type": "thread",
        "name": "Thread Generator",
        "description": "Multi-tweet Twitter/X and Threads content split to platform limits",
        "text_model": CF_TEXT_FREE,
        "hf_text_fallback": HF_TEXT_FALLBACK,
    },
    "story": {
        "content_type": "story",
        "name": "Story Creator",
        "description": "Instagram and Facebook story images generated with FLUX",
        "txt2img_model": CF_TXT2IMG_FREE,
        "hf_txt2img_fallback": HF_TXT2IMG_FALLBACK,
        "txt2img_steps": 4,
    },
    "poll": {
        "content_type": "poll",
        "name": "Poll Generator",
        "description": "Engaging polls with AI-generated question and options",
        "text_model": CF_TEXT_FREE,
        "hf_text_fallback": HF_TEXT_FALLBACK,
    },
    "article": {
        "content_type": "article",
        "name": "Article Publisher",
        "description": "Long-form LinkedIn articles with AI-structured content",
        "text_model": CF_TEXT_FREE,
        "hf_text_fallback": HF_TEXT_FALLBACK,
    },
}

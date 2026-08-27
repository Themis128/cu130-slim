"""Cloudflare Workers AI model defaults optimized for the free neuron pool.

All Workers AI models share the same daily free allocation (10,000 Neurons).
There is no unlimited "free model" — these IDs are free-*tier eligible* and
low neuron cost so a carousel run is more likely to fit in the daily budget.

Pricing reference: https://developers.cloudflare.com/workers-ai/platform/pricing/
"""

from __future__ import annotations

# Cheapest capable text models (neurons / M tokens). Prefer small instruct models.
CF_TEXT_FREE = "@cf/meta/llama-3.2-3b-instruct"
# Tiny fallback if 3B is unavailable / capacity-limited
CF_TEXT_FREE_TINY = "@cf/meta/llama-3.2-1b-instruct"

# Cheapest image generation in the catalog (neurons per tile + per step)
CF_TXT2IMG_FREE = "@cf/black-forest-labs/flux-1-schnell"

# Legacy SD img2img — available on free tier; far cheaper than FLUX.2 klein edit
CF_IMG2IMG_FREE = "@cf/runwayml/stable-diffusion-v1-5-img2img"

# Avoid as default on free tier (higher neuron cost):
#   @cf/black-forest-labs/flux-2-klein-4b
#   @cf/black-forest-labs/flux-2-klein-9b
#   @cf/meta/llama-3.1-8b-instruct (non-fp8)
# Paid-only frontier models (403 on free): kimi-k2.6, glm-5.2, etc.

CF_CAROUSEL_DEFAULTS = {
    "text_model": CF_TEXT_FREE,
    "txt2img_model": CF_TXT2IMG_FREE,
    "img2img_model": CF_IMG2IMG_FREE,
    "txt2img_steps": 4,
    "img2img_steps": 8,
}

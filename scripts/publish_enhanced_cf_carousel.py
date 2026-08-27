#!/usr/bin/env python3
"""CF-only enhanced carousel: LLM → FLUX txt2img → SD img2img → brand → LinkedIn company page."""

from __future__ import annotations

import asyncio
import base64
import io
import os
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.core.config import settings
from app.services.inference import _call_cf_image_pipeline
from app.services.plain_english import run_nlp_check_and_fix

ORG_ACCOUNT_ID = "4a8d9440-47d2-4bda-bd11-3776fd9022ba"
TEXT_MODEL = "@cf/meta/llama-3.2-3b-instruct"
TXT2IMG = "@cf/black-forest-labs/flux-1-schnell"
IMG2IMG = "@cf/runwayml/stable-diffusion-v1-5-img2img"
OUT = Path("/tmp/cloudless_enhanced_carousel")

TOPIC = (
    "cloudless.gr helps small teams (2–20 people) get cloud, data, and AI marketing done without the usual hassle. "
    "Clear skies. Zero friction. Founded by Themistoklis Baltzakis (Cloudflare Certified). "
    "What we do: move you to the cloud, build serverless apps (Workers/D1/R2), set up dashboards, and run AI marketing. "
    "Why teams pick us: results in 14 days, no long contracts, your code stays yours, clear pricing. "
    "Next step: free audit at www.cloudless.gr"
)

FALLBACK_PROMPTS = [
    ("cover", "Clear skies. Zero friction.", "Cloud help for small teams — without the lock-in.",
     "professional dark navy abstract cloud and lightning motif, cyan and soft orange accents, square"),
    ("content", "Results in 14 days", "You should see real progress within two weeks.",
     "abstract rising metrics dashboard glow cyan on dark navy, square"),
    ("content", "No long contracts", "Month to month. Stop anytime. Your code stays yours.",
     "open doorway of light on dark navy, freedom metaphor cyan rim light, square"),
    ("content", "Apps that scale", "Workers, D1, R2 — cheap when quiet, ready when busy.",
     "futuristic serverless nodes connected by light beams, cyan orange on navy, square"),
    ("stat", "Built for uptime", "Made for teams that cannot afford downtime.",
     "glowing uptime shield and signal waves, cyan on dark navy, square"),
    ("content", "One partner for growth", "Cloud, dashboards, and AI marketing in one place.",
     "four interlocking abstract blocks cloud code data marketing, cyan orange navy, square"),
    ("cta", "Book a free 30-min audit", "Go to www.cloudless.gr — clear advice, no hard sell.",
     "clear sky breaking through clouds, hopeful cyan sunrise on dark navy, square"),
]

BG = (11, 18, 32)
ACCENT = (34, 211, 230)
ACCENT2 = (251, 146, 60)
TEXT = (221, 228, 240)
SUB = (136, 149, 172)


def font(size: int, bold: bool = False):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for p in paths:
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def draw_wrapped(draw, text, xy, font_obj, fill, max_width):
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if draw.textlength(test, font=font_obj) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font_obj, fill=fill)
        y += int(font_obj.size * 1.25)
    return y


def compose_slide(bg_img: Image.Image, index: int, total: int, stype: str, title: str, body: str) -> Image.Image:
    img = bg_img.convert("RGB").resize((1080, 1080), Image.Resampling.LANCZOS)
    overlay = Image.new("RGB", img.size, BG)
    img = Image.blend(img, overlay, 0.58)
    draw = ImageDraw.Draw(img)
    for x in range(1080):
        t = x / 1079
        color = (
            int(ACCENT[0] * (1 - t) + ACCENT2[0] * t),
            int(ACCENT[1] * (1 - t) + ACCENT2[1] * t),
            int(ACCENT[2] * (1 - t) + ACCENT2[2] * t),
        )
        draw.line([(x, 0), (x, 5)], fill=color)
    brand_font = font(34, True)
    draw.text((80, 56), "cloudless", font=brand_font, fill=ACCENT)
    draw.text((80 + draw.textlength("cloudless", font=brand_font), 56), ".gr", font=font(34), fill=SUB)
    draw.text((900, 62), f"{index:02d} / {total:02d}", font=font(26), fill=SUB)
    y = 280 if stype == "cover" else 320
    if stype == "cover":
        draw.rounded_rectangle((80, 200, 640, 260), radius=30, outline=ACCENT, width=2)
        draw.text((110, 214), "Clear skies. Zero friction.", font=font(24, True), fill=ACCENT)
    y = draw_wrapped(draw, title, (80, y), font(56, True), TEXT, 920)
    y += 28
    draw_wrapped(draw, body, (80, y), font(30), SUB, 920)
    draw.text((80, 990), "www.cloudless.gr", font=font(24), fill=ACCENT)
    return img


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=600.0) as client:
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": settings.SOCIAL_ADMIN_EMAIL, "password": settings.SOCIAL_ADMIN_PASSWORD},
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        accounts = (await client.get("/api/v1/accounts", headers=headers)).json()
        org = next(
            (a for a in accounts if a.get("id") == ORG_ACCOUNT_ID and a.get("status") == "active"),
            None,
        )
        if not org:
            org = next(
                (
                    a
                    for a in accounts
                    if a.get("platform") == "linkedin"
                    and (a.get("account_type") == "organization" or (a.get("meta_data") or {}).get("account_type") == "organization")
                    and a.get("status") == "active"
                ),
                None,
            )
        if not org:
            raise SystemExit("cloudless.gr LinkedIn organization account not connected")
        print("posting_as", org.get("display_name"), org["id"])

        car = await client.post(
            "/api/v1/ai/generate-carousel",
            headers=headers,
            json={
                "topic": TOPIC,
                "num_slides": 7,
                "platform": "linkedin",
                "tone": "clear and friendly",
                "include_cta": True,
                "provider": "cloudflare",
                "model": TEXT_MODEL,
            },
        )
        print("carousel_http", car.status_code)
        car.raise_for_status()
        carousel = car.json()
        slides = list(carousel.get("slides") or [])
        while len(slides) < 7:
            st, title, body, _ = FALLBACK_PROMPTS[len(slides)]
            slides.append({"slide_type": st, "title": title, "body": body, "highlight": None})
        slides = slides[:7]
        caption = carousel.get("suggested_caption") or "Grow your business with cloudless.gr"
        hashtags = carousel.get("hashtags") or ["cloudless", "serverless", "cloudflare"]

        # NLP checker + fixer (always)
        slides, caption, nlp_report = await run_nlp_check_and_fix(
            slides=slides,
            caption=caption,
            provider_name="cloudflare",
            model=TEXT_MODEL,
            force_fix=True,
        )
        print("nlp_report", nlp_report.to_dict())
        print("slides", [(s.get("slide_type"), s.get("title")) for s in slides])

        media_ids: list[str] = []
        for i, slide in enumerate(slides):
            stype = slide.get("slide_type") or FALLBACK_PROMPTS[i][0]
            title = slide.get("title") or FALLBACK_PROMPTS[i][1]
            body = slide.get("body") or FALLBACK_PROMPTS[i][2]
            visual = (
                f"LinkedIn carousel background for '{title}'. {FALLBACK_PROMPTS[i][3]}. "
                "No readable text, no logos, square composition."
            )
            enhance = (
                f"Improve quality and content for a premium LinkedIn carousel about '{title}'. "
                f"Sharper details, richer cyan and orange lighting on dark navy, stronger visual metaphor for: {body}. "
                "No text overlays, no logos, professional polish."
            )
            print(f"pipeline {i + 1}/7 txt2img={TXT2IMG} img2img={IMG2IMG}")
            pipe = await _call_cf_image_pipeline(
                prompt=visual,
                enhance_prompt=enhance,
                txt2img_model=TXT2IMG,
                img2img_model=IMG2IMG,
                strength=0.42,
                txt2img_steps=4,
                img2img_steps=15,
            )
            bg = Image.open(io.BytesIO(base64.b64decode(pipe["image_base64"])))
            final = compose_slide(bg, i + 1, 7, stype, title, body)
            path = OUT / f"slide-{i + 1:02d}.png"
            final.save(path, format="PNG", optimize=True)
            with path.open("rb") as handle:
                up = await client.post(
                    "/api/v1/media/upload",
                    headers=headers,
                    files={"file": (path.name, handle, "image/png")},
                    data={
                        "alt_text": f"Cloudless enhanced carousel slide {i + 1}: {title}",
                        "tags": "carousel,cloudless,cf-pipeline,enhanced",
                    },
                )
            print("upload", i + 1, up.status_code)
            up.raise_for_status()
            media_ids.append(up.json()["id"])

        full_caption = (
            caption.strip()
            + "\n\n"
            + " ".join("#" + t.lstrip("#") for t in hashtags)
            + "\n\nwww.cloudless.gr"
        )
        create = await client.post(
            "/api/v1/content/posts",
            headers=headers,
            json={
                "content_text": full_caption,
                "hashtags": hashtags,
                "media_ids": media_ids,
                "target_account_ids": [org["id"]],
                "link_url": "https://www.cloudless.gr",
                "metadata": {
                    "carousel": {
                        "theme": "cloudless",
                        "pipeline": "cf-txt2img-img2img",
                        "txt2img_model": TXT2IMG,
                        "img2img_model": IMG2IMG,
                        "text_model": TEXT_MODEL,
                        "num_slides": 7,
                        "account": "cloudless.gr",
                        "slides": slides,
                    }
                },
            },
        )
        print("create_http", create.status_code)
        if create.status_code >= 400:
            print(create.text[:500])
            create.raise_for_status()
        post_id = create.json()["id"]
        print("post_id", post_id)

        pub = await client.post(f"/api/v1/content/posts/{post_id}/publish-now", headers=headers)
        print("publish_http", pub.status_code, pub.json().get("status"))
        if pub.status_code >= 400:
            print(pub.text[:500])
            pub.raise_for_status()

        for attempt in range(50):
            await asyncio.sleep(3)
            post = (await client.get(f"/api/v1/content/posts/{post_id}", headers=headers)).json()
            targets = post.get("targets") or []
            print(
                f"attempt {attempt + 1}",
                post.get("status"),
                [(t.get("status"), t.get("platform_url"), t.get("error") or t.get("error_message")) for t in targets],
                "fail",
                post.get("failure_reason"),
            )
            if post.get("status") in {"published", "failed"} or any(
                (t.get("status") or "").lower() in {"published", "failed"} for t in targets
            ):
                break


if __name__ == "__main__":
    asyncio.run(main())

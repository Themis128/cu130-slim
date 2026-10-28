#!/usr/bin/env python3
"""Generate a 7-slide Cloudless carousel with CF Workers AI FLUX and publish to LinkedIn."""

from __future__ import annotations

import asyncio
import base64
import io
import os
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont

from app.core.config import settings
from app.services.inference import _call_workers_ai_image

MODEL = "@cf/black-forest-labs/flux-1-schnell"
OUT = Path("/tmp/cloudless_carousel")
HOST_OUT = Path("/app/uploads/cloudless-carousel")

TOPIC = (
    "cloudless.gr — Clear skies. Zero friction. Enterprise-grade serverless cloud, "
    "data analytics, and AI marketing for 2–20 person teams. Founded by Themistoklis Baltzakis "
    "(Cloudflare Certified). Services: Cloud Architecture & Migration, Serverless Development "
    "(Workers/D1/R2), Data Analytics & Dashboards, AI & Digital Marketing. Value props: results "
    "in 14 days, no lock-in, your code is yours, transparent pricing. CTA: free audit at www.cloudless.gr"
)

SLIDE_PROMPTS = [
    (
        "cover",
        "Clear skies. Zero friction.",
        "Enterprise cloud for 2–20 person teams — without enterprise lock-in.",
        "professional LinkedIn carousel cover background, dark navy abstract cloud and lightning motif, cyan and soft orange accents, clean modern tech aesthetic, no readable text, square composition",
    ),
    (
        "content",
        "Results in 14 days",
        "Measurable progress within two weeks of kickoff — or we keep working until you see it.",
        "abstract dashboard metrics rising upward, dark navy UI glow, cyan data lines, minimal tech illustration, no text, square composition",
    ),
    (
        "content",
        "No lock-in contracts",
        "Month-to-month. Cancel anytime. Your infrastructure and code stay yours.",
        "symbolic open door made of soft light on dark navy background, freedom and ownership metaphor, cyan rim light, no text, square",
    ),
    (
        "content",
        "Serverless that scales",
        "Workers, D1, R2 — apps that cost almost nothing when idle and scale automatically.",
        "futuristic serverless cloud nodes connected by light beams, cyan and orange palette on dark navy, no text, square",
    ),
    (
        "stat",
        "99.9% uptime mindset",
        "Production-grade reliability designed for startups that cannot afford downtime.",
        "glowing uptime shield and signal waves on dark navy, cyan highlights, reliability metaphor, no text, square",
    ),
    (
        "content",
        "Full stack for growth",
        "Cloud architecture, analytics dashboards, and AI marketing — one partner for scale.",
        "four abstract interlocking blocks representing cloud, code, data, and marketing, dark navy with cyan orange accents, no text, square",
    ),
    (
        "cta",
        "Book a free 30-min audit",
        "Visit www.cloudless.gr — actionable insights, no pitch required.",
        "clear sky breaking through clouds, hopeful cyan sunrise on dark navy, call to action mood, no readable text, square",
    ),
]

BG = (11, 18, 32)
ACCENT = (34, 211, 230)
ACCENT2 = (251, 146, 60)
TEXT = (221, 228, 240)
SUB = (136, 149, 172)


def font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def draw_wrapped(draw, text, xy, font_obj, fill, max_width):
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        test = (cur + " " + word).strip()
        if draw.textlength(test, font=font_obj) <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
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
    img = Image.blend(img, overlay, 0.62)
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
    y = draw_wrapped(draw, title, (80, y), font(58, True), TEXT, 920)
    y += 28
    draw_wrapped(draw, body, (80, y), font(32), SUB, 920)
    draw.text((80, 990), "www.cloudless.gr", font=font(24), fill=ACCENT)
    return img


async def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    HOST_OUT.mkdir(parents=True, exist_ok=True)

    async with httpx.AsyncClient(base_url="http://127.0.0.1:8000", timeout=300.0) as client:
        login = await client.post(
            "/api/v1/auth/login",
            data={"username": settings.SOCIAL_ADMIN_EMAIL, "password": settings.SOCIAL_ADMIN_PASSWORD},
        )
        login.raise_for_status()
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

        accounts = (await client.get("/api/v1/accounts", headers=headers)).json()
        linkedin = next((a for a in accounts if a.get("platform") == "linkedin" and a.get("status") == "active"), None)
        if not linkedin:
            raise SystemExit("LinkedIn account is not connected")
        print("linkedin_account", linkedin.get("display_name"))

        carousel_resp = await client.post(
            "/api/v1/ai/generate-carousel",
            headers=headers,
            json={
                "topic": TOPIC,
                "num_slides": 7,
                "platform": "linkedin",
                "tone": "professional",
                "include_cta": True,
                "provider": "cloudflare",
                "model": "@cf/meta/llama-3.2-3b-instruct",
            },
        )
        print("carousel_http", carousel_resp.status_code)
        carousel_resp.raise_for_status()
        carousel = carousel_resp.json()
        slides = list(carousel.get("slides") or [])
        while len(slides) < 7:
            st, title, body, _ = SLIDE_PROMPTS[len(slides)]
            slides.append({"slide_type": st, "title": title, "body": body, "highlight": None})
        slides = slides[:7]
        caption = carousel.get("suggested_caption") or "Transform your stack with cloudless.gr"
        hashtags = carousel.get("hashtags") or ["cloudless", "serverless", "cloudflare"]
        print("slides", [(s.get("slide_type"), s.get("title")) for s in slides])

        media_ids: list[str] = []
        for i, slide in enumerate(slides):
            stype = slide.get("slide_type") or SLIDE_PROMPTS[i][0]
            title = slide.get("title") or SLIDE_PROMPTS[i][1]
            body = slide.get("body") or SLIDE_PROMPTS[i][2]
            prompt = SLIDE_PROMPTS[i][3]
            print(f"generating_image {i + 1}/7 model={MODEL}")
            img_res = await _call_workers_ai_image(
                prompt=prompt,
                model=MODEL,
                api_key=None,
                steps=4,
            )
            bg = Image.open(io.BytesIO(base64.b64decode(img_res["image_base64"])))
            final = compose_slide(bg, i + 1, 7, stype, title, body)
            path = OUT / f"slide-{i + 1:02d}.png"
            final.save(path, format="PNG", optimize=True)
            final.save(HOST_OUT / path.name, format="PNG", optimize=True)
            with path.open("rb") as handle:
                upload = await client.post(
                    "/api/v1/media/upload",
                    headers=headers,
                    files={"file": (path.name, handle, "image/png")},
                    data={"alt_text": f"Cloudless carousel slide {i + 1}: {title}", "tags": "carousel,cloudless,workers-ai"},
                )
            print("upload", i + 1, upload.status_code)
            upload.raise_for_status()
            media_ids.append(upload.json()["id"])

        full_caption = (
            caption.strip()
            + "\n\n"
            + " ".join("#" + tag.lstrip("#") for tag in hashtags)
            + "\n\nwww.cloudless.gr"
        )
        create = await client.post(
            "/api/v1/content/posts",
            headers=headers,
            json={
                "content_text": full_caption,
                "hashtags": hashtags,
                "media_ids": media_ids,
                "target_account_ids": [linkedin["id"]],
                "link_url": "https://www.cloudless.gr",
                "metadata": {
                    "carousel": {
                        "theme": "cloudless",
                        "provider": "cloudflare",
                        "image_model": MODEL,
                        "num_slides": 7,
                        "topic": "cloudless.gr",
                        "slides": slides,
                    }
                },
            },
        )
        print("create_http", create.status_code)
        if create.status_code >= 400:
            print("create_body", create.text[:500])
            create.raise_for_status()
        post_id = create.json()["id"]
        print("post_id", post_id)

        publish = await client.post(f"/api/v1/content/posts/{post_id}/publish-now", headers=headers)
        print("publish_http", publish.status_code)
        if publish.status_code >= 400:
            print("publish_body", publish.text[:500])
            publish.raise_for_status()
        print("post_status", publish.json().get("status"))

        for attempt in range(40):
            await asyncio.sleep(3)
            history = await client.get("/api/v1/publishing/history", headers=headers, params={"page_size": 20})
            items = history.json()
            if isinstance(items, dict):
                items = items.get("items") or items.get("history") or items.get("data") or []
            mine = [x for x in items if str(x.get("post_id")) == str(post_id)]
            queue = await client.get("/api/v1/publishing/queue", headers=headers, params={"page_size": 20})
            qitems = queue.json()
            if isinstance(qitems, dict):
                qitems = qitems.get("items") or qitems.get("queue") or qitems.get("data") or []
            qmine = [x for x in qitems if str(x.get("post_id")) == str(post_id)]
            statuses = [
                (
                    x.get("status"),
                    x.get("error") or x.get("failure_reason"),
                    x.get("platform_url") or x.get("platform_post_id"),
                )
                for x in (mine + qmine)
            ]
            print("attempt", attempt + 1, "statuses", statuses[:6])
            if any((s[0] or "").lower() in {"completed", "published", "success", "failed"} for s in statuses):
                break

        post = (await client.get(f"/api/v1/content/posts/{post_id}", headers=headers)).json()
        print("final_post_status", post.get("status"))
        print("failure_reason", post.get("failure_reason"))
        for target in post.get("targets") or []:
            print(
                "target",
                {
                    "status": target.get("status"),
                    "platform_post_id": target.get("platform_post_id"),
                    "platform_url": target.get("platform_url"),
                    "error": target.get("error") or target.get("failure_reason"),
                },
            )


if __name__ == "__main__":
    asyncio.run(main())

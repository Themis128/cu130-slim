"""End-to-end Cloudflare carousel pipeline for n8n / automation."""

from __future__ import annotations

import base64
import io
import os
import uuid
from datetime import UTC, datetime

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.content import Post, PostStatus, PostTarget
from app.models.social_account import SocialAccount
from app.models.user import Team, User
from app.services.cf_models import CF_CAROUSEL_DEFAULTS, CF_IMG2IMG_FREE, CF_TEXT_FREE, CF_TXT2IMG_FREE
from app.services.inference import _call_cf_image_pipeline, call_inference
from app.services.media_storage import persist_generated_image
from app.services.plain_english import (
    PLAIN_ENGLISH_RULES,
    build_linkedin_caption,
    run_nlp_check_and_fix,
)
from app.services.duplicate_detector import is_duplicate

DEFAULT_ORG_ACCOUNT_ID = os.environ.get(
    "CLOUDLESS_LINKEDIN_ORG_ACCOUNT_ID",
    "4a8d9440-47d2-4bda-bd11-3776fd9022ba",
)

BG = (11, 18, 32)
ACCENT = (34, 211, 230)
ACCENT2 = (251, 146, 60)
TEXT = (221, 228, 240)
SUB = (136, 149, 172)


def _font(size: int, bold: bool = False):
    path = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
    )
    if os.path.exists(path):
        return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _draw_wrapped(draw, text, xy, font_obj, fill, max_width):
    words = text.split()
    lines, cur = [], ""
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


def compose_branded_slide(
    bg_img: Image.Image,
    *,
    index: int,
    total: int,
    slide_type: str,
    title: str,
    body: str,
) -> Image.Image:
    """Draw NLP copy only — skip body when it repeats the title."""
    title = (title or "").strip()
    body = (body or "").strip()
    if body and title and is_duplicate(title, body):
        # Keep the longer NLP line once.
        if len(body) >= len(title):
            title, body = body, ""
        else:
            body = ""

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
    brand_font = _font(34, True)
    draw.text((80, 56), "cloudless", font=brand_font, fill=ACCENT)
    draw.text((80 + draw.textlength("cloudless", font=brand_font), 56), ".gr", font=_font(34), fill=SUB)
    draw.text((900, 62), f"{index:02d} / {total:02d}", font=_font(26), fill=SUB)
    y = 280 if slide_type == "cover" else 320
    if slide_type == "cover":
        draw.rounded_rectangle((80, 200, 640, 260), radius=30, outline=ACCENT, width=2)
        draw.text((110, 214), "Clear skies. Zero friction.", font=_font(24, True), fill=ACCENT)
    if title:
        title_size = 48 if len(title) > 60 else 56
        y = _draw_wrapped(draw, title, (80, y), _font(title_size, True), TEXT, 920)
        y += 28
    if body:
        _draw_wrapped(draw, body, (80, y), _font(30), SUB, 920)
    draw.text((80, 990), "www.cloudless.gr", font=_font(24), fill=ACCENT)
    return img


async def generate_carousel_copy(
    *,
    topic: str,
    num_slides: int,
    tone: str,
    include_cta: bool,
    text_model: str,
    db: AsyncSession,
    team_id,
) -> dict:
    num = max(3, min(10, num_slides))
    prompt = f"""Create a {num}-slide infographic carousel about: "{topic}"

Platform: linkedin — each slide delivers one clear idea in plain English.
Tone: {tone} (still use plain everyday English)
{"The last slide should be a strong CTA." if include_cta else ""}

{PLAIN_ENGLISH_RULES}

Return JSON with:
- slides: array of exactly {num} objects with title, body (max 100 chars), highlight (string|null), slide_type (cover|content|stat|cta)
- suggested_caption: plain English caption
- hashtags: array of 5-8 hashtags without #"""

    schema = {
        "type": "object",
        "properties": {
            "slides": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "highlight": {"type": ["string", "null"]},
                        "slide_type": {"type": "string"},
                    },
                    "required": ["title", "body", "slide_type"],
                },
            },
            "suggested_caption": {"type": "string"},
            "hashtags": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["slides", "suggested_caption", "hashtags"],
    }
    return await call_inference(
        prompt,
        provider_name="cloudflare",
        db=db,
        team_id=team_id,
        schema=schema,
        model_override=text_model,
    )


async def run_cloudless_carousel_pipeline(
    *,
    db: AsyncSession,
    user: User,
    team: Team,
    topic: str,
    num_slides: int = 7,
    tone: str = "clear and friendly",
    include_cta: bool = True,
    text_model: str = CF_TEXT_FREE,
    txt2img_model: str = CF_TXT2IMG_FREE,
    img2img_model: str = CF_IMG2IMG_FREE,
    strength: float = 0.42,
    target_account_id: str | None = None,
    publish: bool = True,
    wait_for_publish: bool = False,
) -> dict:
    """Full pipeline: copy → NLP check/fix → CF images → brand → post → optional publish."""
    target_id = uuid.UUID(target_account_id or DEFAULT_ORG_ACCOUNT_ID)
    account = (
        await db.execute(
            select(SocialAccount).where(
                SocialAccount.id == target_id,
                SocialAccount.team_id == team.id,
                SocialAccount.status == "active",
            )
        )
    ).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=400, detail=f"LinkedIn target account not found: {target_id}")

    # 1) Copy
    raw = await generate_carousel_copy(
        topic=topic,
        num_slides=num_slides,
        tone=tone,
        include_cta=include_cta,
        text_model=text_model,
        db=db,
        team_id=team.id,
    )
    slides = list(raw.get("slides") or [])[:num_slides]
    caption = raw.get("suggested_caption") or "Grow your business with cloudless.gr"
    hashtags = raw.get("hashtags") or ["cloudless", "serverless", "cloudflare"]

    # 2) NLP checker + fixer
    slides, caption, nlp_report = await run_nlp_check_and_fix(
        slides=slides,
        caption=caption,
        provider_name="cloudflare",
        model=text_model,
        db=db,
        team_id=team.id,
        force_fix=True,
    )

    # 3) Images + branding
    media_ids: list[uuid.UUID] = []
    for i, slide in enumerate(slides):
        title = slide.get("title") or f"Slide {i + 1}"
        body = slide.get("body") or ""
        stype = slide.get("slide_type") or "content"
        visual = (
            f"LinkedIn carousel background for '{title}'. {body}. "
            "Dark navy abstract tech aesthetic, cyan and soft orange accents, square, no text, no logos."
        )
        enhance = (
            f"Improve image 0 for a LinkedIn carousel about '{title}'. "
            f"Sharper details, richer lighting, stronger metaphor for: {body}. No text, no logos."
        )
        print(f"[n8n-pipeline] slide {i + 1}/{len(slides)} images", flush=True)
        pipe = await _call_cf_image_pipeline(
            prompt=visual,
            enhance_prompt=enhance,
            txt2img_model=txt2img_model,
            img2img_model=img2img_model,
            strength=strength,
            txt2img_steps=CF_CAROUSEL_DEFAULTS["txt2img_steps"],
            img2img_steps=CF_CAROUSEL_DEFAULTS["img2img_steps"],
        )
        bg = Image.open(io.BytesIO(base64.b64decode(pipe["image_base64"])))
        branded = compose_branded_slide(
            bg,
            index=i + 1,
            total=len(slides),
            slide_type=stype,
            title=title,
            body=body,
        )
        buf = io.BytesIO()
        branded.save(buf, format="PNG", optimize=True)
        asset = await persist_generated_image(
            db,
            team_id=team.id,
            user_id=user.id,
            image_bytes=buf.getvalue(),
            prompt=f"n8n-carousel:{title}",
            source="n8n-cf-pipe",
        )
        media_ids.append(asset.id)

    full_caption = build_linkedin_caption(caption, hashtags)

    post = Post(
        team_id=team.id,
        user_id=user.id,
        status=PostStatus.DRAFT,
        content_text=full_caption,
        media_ids=media_ids,
        hashtags=hashtags,
        link_url="https://www.cloudless.gr",
        meta_data={
            "carousel": {
                "pipeline": "n8n-cf-txt2img-img2img-nlp",
                "txt2img_model": txt2img_model,
                "img2img_model": img2img_model,
                "text_model": text_model,
                "nlp": nlp_report.to_dict(),
                "slides": slides,
                "account": account.display_name,
            }
        },
    )
    db.add(post)
    await db.flush()
    db.add(PostTarget(post_id=post.id, social_account_id=account.id, status="pending"))
    await db.commit()
    await db.refresh(post)

    result = {
        "post_id": str(post.id),
        "media_ids": [str(m) for m in media_ids],
        "slides": slides,
        "caption": caption,
        "hashtags": hashtags,
        "nlp_report": nlp_report.to_dict(),
        "target_account": {
            "id": str(account.id),
            "display_name": account.display_name,
            "account_type": (account.meta_data or {}).get("account_type"),
        },
        "status": post.status.value,
        "platform_url": None,
    }

    if not publish:
        return result

    # Mark scheduled so Celery beat can publish even if .delay() flakes.
    from app.worker.celery_app import celery_app
    from app.worker.tasks.publishing import process_publish_queue, publish_post_now

    post.status = PostStatus.SCHEDULED
    post.scheduled_at = datetime.now(UTC)
    await db.commit()
    result["status"] = post.status.value

    queue_error: str | None = None
    try:
        with celery_app.connection_or_acquire() as conn:
            publish_post_now.apply_async(
                args=[str(post.id), [str(account.id)]],
                connection=conn,
            )
            process_publish_queue.apply_async(connection=conn, countdown=2)
    except Exception as exc:  # noqa: BLE001 — still return post; beat may pick it up
        queue_error = f"{type(exc).__name__}: {exc}"
        print(f"[n8n-pipeline] celery queue failed (beat may still publish): {queue_error}", flush=True)
        result["queue_warning"] = queue_error

    if not wait_for_publish:
        return result

    # Optional brief poll (disabled by default for n8n so the webhook returns 200 quickly)
    import asyncio

    for _ in range(40):
        await asyncio.sleep(3)
        refreshed = (
            await db.execute(
                select(Post)
                .options(selectinload(Post.targets))
                .where(Post.id == post.id)
            )
        ).scalar_one()
        await db.refresh(refreshed)
        for t in refreshed.targets:
            await db.refresh(t)
        result["status"] = refreshed.status.value
        result["failure_reason"] = refreshed.failure_reason
        urls = [t.platform_url for t in refreshed.targets if t.platform_url]
        if urls:
            result["platform_url"] = urls[0]
        if refreshed.status in (PostStatus.PUBLISHED, PostStatus.FAILED):
            break

    return result

"""Watch the Sofia Kakkava Visibility Era Challenge Skool community.

Runs every 3 hours via celery-beat on the default queue. Uses the shared
browser-novnc bridge (the Skool session lives in the persistent
``browser_profile`` volume) to:

1. Scrape the VEC classroom module list — new modules = new lessons.
2. Scrape the community feed for new posts (announcements, threads).
3. For every new DAY lesson: extract the lesson text, generate a
   LinkedIn-personal draft through the brand-voice inference path (which
   already injects ``voice_signature`` — creator type, post blueprint,
   niche), and store it as a SocialAuto draft for review.
4. Slack: new DAY lessons go to the alert channel (actionable — today's
   task), other changes go to the digest channel.

State lives in Redis so the task is idempotent across restarts.
Kill switch: ``SET skool:vec:disabled 1``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from sqlalchemy import select

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

_COURSE_URL = "https://www.skool.com/sofia-kakkava-coaching/classroom/0322497b"
_COMMUNITY_URL = "https://www.skool.com/sofia-kakkava-coaching"
_LESSON_URL = "https://www.skool.com/sofia-kakkava-coaching/classroom/0322497b?md={md}"

_SEEN_MODULES = "skool:vec:seen_modules"
_SEEN_POSTS = "skool:vec:seen_posts"
_SEEDED = "skool:vec:seeded"
_LESSON_KEY = "skool:vec:lesson:{md}"
_DISABLED = "skool:vec:disabled"
_SESSION_ALERT = "skool:vec:session_alert"  # TTL'd flag so expiry alerts once/day

# Click each collapsed week header once (innermost element carrying the
# "Week N:" label — clicking an ancestor again would re-collapse it).
_JS_EXPAND_WEEKS = """(() => {
  const els = [...document.querySelectorAll('div,li,section,button')]
    .filter(e => /^Week \\d+:/.test((e.innerText || '').trim()));
  const inner = els.filter(e => !els.some(o => o !== e && e.contains(o)));
  inner.forEach(e => e.click());
  return inner.length;
})()"""

_JS_MODULES = """(() => {
  const seen = new Set();
  const out = [...document.querySelectorAll('a[href*="md="]')]
    .map(a => ({t: (a.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
                h: a.getAttribute('href')}))
    .filter(l => { if (seen.has(l.h)) return false; seen.add(l.h); return true; });
  return JSON.stringify(out);
})()"""

# Community post permalinks look like /sofia-kakkava-coaching/<slug>.
_JS_COMMUNITY_POSTS = """(() => {
  const nav = new Set(['Community','Classroom','Calendar','Members','Leaderboards','About']);
  const seen = new Set();
  const out = [...document.querySelectorAll('a')]
    .map(a => ({t: (a.innerText || '').trim().replace(/\\s+/g, ' ').slice(0, 140),
                h: a.getAttribute('href')}))
    .filter(l => l.h && /^\\/sofia-kakkava-coaching\\/[a-z0-9-]{6,}$/.test(l.h)
                 && l.t.length > 8 && !nav.has(l.t)
                 && !/Sofia Kakkava Coaching$/.test(l.t))
    .filter(l => { if (seen.has(l.h)) return false; seen.add(l.h); return true; });
  return JSON.stringify(out);
})()"""

_JS_TEXT = "(() => JSON.stringify({url: location.href, text: (document.body.innerText || '').slice(0, 12000)}))()"

_MD_RE = re.compile(r"md=([0-9a-f]{32})")
_TASK_RE = re.compile(r"YOUR TASK(.+?)(?:COMMUNITY ACTION|Resources|🔁|$)", re.DOTALL | re.IGNORECASE)


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


async def _redis():
    import redis.asyncio as aioredis

    from app.core.config import get_settings

    return aioredis.from_url(get_settings().REDIS_URL, decode_responses=True)


async def _eval_json(client, expression: str):
    res = await client.evaluate(expression)
    raw = res.get("result") or ""
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return raw


def _extract_task(lesson_text: str) -> str:
    m = _TASK_RE.search(lesson_text)
    if not m:
        return ""
    task = re.sub(r"\s+", " ", m.group(1)).strip()
    return task[:1200]


async def _acquire_browser(client) -> bool:
    """Take over the shared browser once its current owner has finished.

    The bridge honors ``force=true`` on /session/start for manual
    recovery, but forcing a live session can kill an in-flight publish
    compose or manual login. Safe to preempt only sessions that already
    finished their work — ``done``/``idle``/``error`` (``done`` sessions
    keep Chromium open indefinitely, which is the common poller
    artifact). ``waiting`` sessions get a non-force start: the bridge
    preempts them itself only once they're stale. Anything else means
    someone is actively using the browser — skip this sweep.
    """
    from app.services.browser_bridge import BrowserBridgeError

    try:
        state = ((await client.session_status()).get("status") or "").lower()
    except Exception:
        state = ""
    if state in {"active", "logged_in", "extracting", "waiting"}:
        force = False
    elif state in {"done", "idle", "error", ""}:
        force = True
    else:
        return False
    try:
        await client.start_session("skool", force=force)
        await asyncio.sleep(4)
        return True
    except BrowserBridgeError:
        return False


async def _create_draft(db, team_id, user_id, account_id, md, title, lesson_text, task):
    """Generate the day's post via the brand-voice path and store a draft."""
    from app.models.content import Post, PostStatus, PostTarget
    from app.services.brand_compliance import load_brand_context
    from app.services.inference import call_inference

    # Never re-draft a lesson that already produced a post.
    exists = await db.execute(
        select(Post.id).where(
            Post.team_id == team_id,
            Post.meta_data["lesson_md"].as_string() == md,
        )
    )
    if exists.scalars().first():
        return None

    _, _, brand_ctx = await load_brand_context(db, team_id)
    prompt = f"""You are Themis Baltzakis writing today's Visibility Era Challenge post.

Lesson: {title}
Today's task: {task or "(see lesson text)"}
Lesson excerpt:
{lesson_text[:1800]}

Write the LinkedIn post that completes today's task. Follow the post_blueprint
structure in the brand voice exactly — hook, context, one insight fully
explained, one specific CTA question. Founder first-person voice.
End the post with the hashtags #sofiakakkavacoach #visibilityerachallenge.
Real outcomes only — if the task asks for data you don't have, keep a short
[TODO: ...] placeholder instead of inventing numbers.
Return only the post text, no preamble."""

    result = await call_inference(
        prompt=prompt,
        provider_name="dmr",
        db=db,
        team_id=team_id,
        brand_context=brand_ctx,
        platform="linkedin",
        max_tokens=700,
    )
    text = (result.get("response") or "").strip()
    if isinstance(result.get("response"), list):
        text = "\n".join(str(p) for p in result["response"]).strip()
    if not text:
        return None

    post = Post(
        team_id=team_id,
        user_id=user_id,
        status=PostStatus.DRAFT,
        content_text=text,
        hashtags=["#sofiakakkavacoach", "#visibilityerachallenge"],
        meta_data={
            "source": "skool_vec_watch",
            "lesson_md": md,
            "lesson_title": title,
            "needs_media": True,
        },
    )
    db.add(post)
    await db.flush()
    db.add(PostTarget(post_id=post.id, social_account_id=account_id))
    await db.commit()
    return str(post.id)


async def _watch() -> dict:
    import redis.asyncio  # noqa: F401  (ensures driver is present)

    from app.db.session import async_session_maker
    from app.models.content import Post  # noqa: F401
    from app.models.social_account import SocialAccount
    from app.models.user import Team, User
    from app.services.browser_bridge import BrowserBridgeClient, BrowserBridgeError
    from app.services.slack_notifications import (
        post_alert_to_slack,
        post_digest_text_to_slack,
    )

    r = await _redis()
    try:
        if await r.get(_DISABLED):
            return {"skipped": True, "reason": "disabled via redis flag"}

        from app.core.config import get_settings

        client = BrowserBridgeClient(get_settings().BROWSER_BRIDGE_URL, platform="skool")

        # ── Session check: logged-out Skool redirects classroom → /about ──
        try:
            await client.navigate(_COURSE_URL)
        except BrowserBridgeError as exc:
            if "No active browser session" not in str(exc) and "busy" not in str(exc).lower():
                logger.warning("skool_watch: bridge unavailable: %s", exc)
                return {"skipped": True, "reason": f"browser unavailable: {exc}"}
            # No session, or a poller's busy-hold is active — take over
            # only when the current owner has finished its work.
            if not await _acquire_browser(client):
                logger.warning("skool_watch: browser busy: %s", exc)
                return {"skipped": True, "reason": f"browser busy: {exc}"}
            await client.navigate(_COURSE_URL)
        await asyncio.sleep(6)
        info = await _eval_json(client, _JS_TEXT)
        url = (info.get("url") or "") if isinstance(info, dict) else ""
        if "/login" in url or url.rstrip("/").endswith("/about") or "/classroom" not in url:
            if not await r.get(_SESSION_ALERT):
                await post_alert_to_slack(
                    "🎓 *Skool session expired* — the VEC watcher can't reach the "
                    "classroom (redirected to the logged-out page). Re-login at "
                    "http://localhost:6080/vnc.html → https://www.skool.com/login, "
                    "then the next sweep resumes automatically."
                )
                await r.set(_SESSION_ALERT, "1", ex=86400)
            return {"skipped": True, "reason": "logged out", "url": url}
        await r.delete(_SESSION_ALERT)

        # ── Classroom: expand weeks, collect module map ───────────────────
        await client.evaluate(_JS_EXPAND_WEEKS)
        await asyncio.sleep(2)
        modules = await _eval_json(client, _JS_MODULES)
        modules = modules if isinstance(modules, list) else []
        seen_mods = set(await r.smembers(_SEEN_MODULES))
        seeded = bool(await r.get(_SEEDED))
        new_modules = []
        for m in modules:
            mm = _MD_RE.search(m.get("h") or "")
            if mm and mm.group(1) not in seen_mods:
                new_modules.append({"md": mm.group(1), "title": m.get("t", "")})

        # ── Community feed: collect post permalinks ───────────────────────
        await client.navigate(_COMMUNITY_URL)
        await asyncio.sleep(5)
        posts = await _eval_json(client, _JS_COMMUNITY_POSTS)
        posts = posts if isinstance(posts, list) else []
        seen_posts = set(await r.smembers(_SEEN_POSTS))
        new_posts = [p for p in posts if (p.get("h") or "") not in seen_posts]

        summary: dict = {
            "modules": len(modules),
            "new_modules": len(new_modules),
            "new_posts": len(new_posts),
            "drafts": [],
        }

        # ── Resolve team/user/target account once ─────────────────────────
        async with async_session_maker() as db:
            team = (await db.execute(select(Team).limit(1))).scalars().first()
            user = None
            if team and team.owner_id:
                user = (await db.execute(select(User).where(User.id == team.owner_id))).scalars().first()
            if user is None:
                user = (await db.execute(select(User).limit(1))).scalars().first()
            acct = (
                (
                    await db.execute(
                        select(SocialAccount).where(
                            SocialAccount.platform == "linkedin",
                            SocialAccount.account_type == "person",
                        )
                    )
                )
                .scalars()
                .first()
            )

            # First sweep seeds the known-state sets silently — no alerts,
            # no drafts — so only genuinely new content is reported.
            if not seeded:
                for mod in new_modules:
                    await r.sadd(_SEEN_MODULES, mod["md"])
                for p in new_posts:
                    await r.sadd(_SEEN_POSTS, p["h"])
                await r.set(_SEEDED, "1")
                summary["seeded"] = True
                summary["new_modules"] = 0
                summary["new_posts"] = 0
                return summary

            # ── Handle new modules ────────────────────────────────────────
            for mod in new_modules:
                md, title = mod["md"], mod["title"]
                lesson_text = ""
                try:
                    await client.navigate(_LESSON_URL.format(md=md))
                    await asyncio.sleep(5)
                    info = await _eval_json(client, _JS_TEXT)
                    if isinstance(info, dict):
                        lesson_text = info.get("text") or ""
                except Exception as exc:  # lesson body is best-effort
                    logger.warning("skool_watch: lesson fetch failed %s: %s", md, exc)

                task = _extract_task(lesson_text)
                await r.hset(
                    _LESSON_KEY.format(md=md),
                    mapping={
                        "title": title,
                        "text": lesson_text[:8000],
                        "task": task,
                        "seen_at": str(int(time.time())),
                    },
                )
                await r.sadd(_SEEN_MODULES, md)

                lesson_url = _LESSON_URL.format(md=md)
                is_day = bool(re.search(r"DAY\s*\d+", title, re.IGNORECASE))
                draft_id = None
                if is_day and team and user and acct:
                    try:
                        draft_id = await _create_draft(db, team.id, user.id, acct.id, md, title, lesson_text, task)
                    except Exception:
                        logger.exception("skool_watch: draft gen failed for %s", md)
                if draft_id:
                    summary["drafts"].append(draft_id)

                if is_day:
                    await post_alert_to_slack(
                        f"🎓 *New VEC lesson:* {title}\n"
                        f"<{lesson_url}|Open on Skool>"
                        + (f"\n*Task:* {task[:400]}" if task else "")
                        + (
                            f"\n📝 Draft post `{draft_id[:8]}` created for LinkedIn personal — review + attach media before publishing."
                            if draft_id
                            else "\n⚠️ No draft generated — check the lesson manually."
                        )
                    )
                else:
                    await post_digest_text_to_slack(f"🎓 New Skool module in VEC: *{title}* — <{lesson_url}|open>")

            # ── Handle new community posts ────────────────────────────────
            for p in new_posts:
                await r.sadd(_SEEN_POSTS, p["h"])
            if new_posts:
                lines = "\n".join(f"• <https://www.skool.com{p['h']}|{p['t'][:80]}>" for p in new_posts[:8])
                await post_digest_text_to_slack(f"🎓 *{len(new_posts)} new post(s)* in the VEC Skool community:\n{lines}")

        return summary
    finally:
        await r.aclose()


@celery_app.task(name="app.worker.tasks.skool_watch.watch_skool_vec")
def watch_skool_vec() -> dict:
    return _run_async(_watch())

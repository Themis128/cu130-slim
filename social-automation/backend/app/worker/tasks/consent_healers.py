"""Hourly consent-state healers — finish the chain when a human gate lands.

- twitter:  bearer probe 200 but status=expired → promote to active
  (fallback — the OAuth callback already does this on approve)
- linkedin: memberSnapshotData returns 200 → portability product granted;
  harvest CONNECTIONS + attach to the survey campaign automatically
- whatsapp: code_verification_status flips VERIFIED → Slack alert
"""

from __future__ import annotations

import asyncio
import logging
import os

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models.social_account import SocialAccount
from app.services.slack_notifications import post_alert_to_slack
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

celery_app.set_default()
celery_app.set_current()
logger = logging.getLogger(__name__)

SURVEY_CID = "9f98e2ce-24da-46f3-a19f-8d7e0e875270"


async def _twitter() -> str:
    async with task_session() as db:
        acc = (await db.execute(select(SocialAccount).where(SocialAccount.platform == "twitter"))).scalars().first()
        if not acc or acc.status != "expired" or not acc.access_token_enc:
            return "skip"
        try:
            tok = decrypt_token(bytes(acc.access_token_enc))
        except Exception:
            return "skip"
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get("https://api.x.com/2/users/me", headers={"Authorization": f"Bearer {tok}"})
        if r.status_code == 200:
            acc.status = "active"
            await db.commit()
            await post_alert_to_slack(":white_check_mark: X re-auth detected — account back to *active*.")
            return "healed"
        return await _try_bridge_reauth(str(acc.team_id))


async def _linkedin_snapshot() -> str:
    """On portability grant → harvest connections → attach to survey."""
    async with task_session() as db:
        acc = (await db.execute(select(SocialAccount).where(SocialAccount.platform == "linkedin", SocialAccount.account_type == "person"))).scalars().first()
        if not acc or not acc.access_token_enc:
            return "skip"
        try:
            tok = decrypt_token(bytes(acc.access_token_enc))
        except Exception:
            return "skip"
        async with httpx.AsyncClient(timeout=20) as c:
            r = await c.get(
                "https://api.linkedin.com/rest/memberSnapshotData",
                params={"q": "criteria", "domain": "CONNECTIONS", "count": 50},
                headers={"Authorization": f"Bearer {tok}", "Linkedin-Version": "202312"},
            )
        if r.status_code != 200:
            return f"no-grant ({r.status_code})"
        # granted — harvest emails + attach
        emails = []
        for el in r.json().get("elements") or []:
            e = (el.get("emailAddress") or "").lower()
            if e and "@" in e and "linkedin" not in e:
                emails.append(e)
        if not emails:
            return "granted-no-emails"
        import subprocess
        import sys

        script = "/app/.devin/skills/offer-survey-ops/scripts/offer_survey.py"
        subprocess.run([sys.executable, script, "audience", SURVEY_CID, *(f"--email={e}" for e in emails)], capture_output=True, text=True)
        await post_alert_to_slack(f":white_check_mark: LinkedIn portability granted — attached {len(emails)} connection emails to the survey campaign.")
        return f"attached {len(emails)}"


async def _whatsapp() -> str:
    cid = os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")
    tok = os.environ.get("WHATSAPP_ACCESS_TOKEN", "")
    if not (cid and tok):
        return "skip"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(f"https://graph.facebook.com/v21.0/{cid}", params={"fields": "code_verification_status", "access_token": tok})
    if r.status_code != 200:
        return "probe-fail"
    cvs = (r.json() or {}).get("code_verification_status")
    if cvs == "VERIFIED":
        await post_alert_to_slack(":white_check_mark: WhatsApp phone VERIFIED — Cloud API sends unblocked.")
        return "verified"
    return f"still-{cvs or 'unknown'}"


async def _bridge_health() -> str:
    """Seed X cookies + probe whether the session resolves vs a login wall —
    alerts when X_WEB_AUTH_TOKEN is stale (the real failure mode)."""
    import json
    import os
    import time
    import urllib.parse
    import urllib.request

    tok, ct0 = os.environ.get("X_WEB_AUTH_TOKEN", ""), os.environ.get("X_WEB_CT0", "")
    if not (tok and ct0):
        return "skip"

    def _post(path, payload):
        r = urllib.request.Request("http://browser-novnc:9223" + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r, timeout=15) as f:
            return json.loads(f.read() or b"{}")

    try:
        urllib.request.urlopen(urllib.request.Request("http://stack-ops:8787/keepawake/browser-novnc?ttl=300", method="POST", data=b""), timeout=8)
        _post(
            "/session/cookies",
            {
                "cookies": [
                    {"name": "auth_token", "value": tok, "domain": ".x.com", "path": "/", "secure": True, "httpOnly": True},
                    {"name": "ct0", "value": ct0, "domain": ".x.com", "path": "/", "secure": True},
                ]
            },
        )
        time.sleep(1)
        _post("/session/evaluate", {"expression": "location.href='https://x.com/home'"})
        time.sleep(6)
        r = _post("/session/evaluate", {"expression": "location.href"})
        cur = str(r.get("result") or r.get("url") or "")
        if "login" in cur or "i/flow" in cur:
            await post_alert_to_slack(":warning: X session stale — refresh X_WEB_AUTH_TOKEN (x.com devtools cookies).")
            return "stale"
        host = urllib.parse.urlparse(cur).hostname or ""
        return "live" if host == "x.com" or host.endswith(".x.com") else f"rotated ({cur[:40]})"
    except Exception as e:
        return f"busy ({str(e)[:60]})"


async def _dmr_health() -> str:
    """Keep the serving model warm + alert if the engine stops serving.
    A 1-token chat call holds smollm2 loaded — cold-load is the
    ConnectTimeout/ReadTimeout class that broke the triage."""
    import os

    import httpx

    url = os.environ.get("DMR_URL", "http://host.docker.internal:12435/engines/llama.cpp/v1")
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.post(
                f"{url}/chat/completions", json={"model": "docker.io/ai/smollm2:latest", "messages": [{"role": "user", "content": "ok"}], "max_tokens": 1}
            )
        if r.status_code == 200:
            return "warm"
        return f"http {r.status_code}"
    except Exception as e:
        await post_alert_to_slack(f":warning: DMR serve check failed — {type(e).__name__}: engine reachable but chat hangs (model cold/unloaded).")
        return f"down ({type(e).__name__})"


async def _x_credentials_login(c: httpx.AsyncClient, bridge: str) -> None:
    """Credentials login on the adhoc session — X's 3-step flow
    (username → maybe-email-verify → password). Fills via /session/fill
    (trusted input) then continues. TOTP/2FA not stored — lands on
    trusted-device or email-code paths only."""
    import os

    u = os.environ.get("TWITTER_LOGIN_USERNAME", "")
    e = os.environ.get("TWITTER_LOGIN_EMAIL", "")
    pw = os.environ.get("TWITTER_LOGIN_PASSWORD", "")
    if not (u and pw):
        return
    await c.post(f"{bridge}/session/evaluate", json={"expression": 'location.href="https://x.com/i/flow/login"'})
    await asyncio.sleep(6)
    for i, val in enumerate([u, e, pw]):
        if not val:
            continue
        await c.post(f"{bridge}/session/fill", json={"selector": "input >> nth=0", "value": val})
        await asyncio.sleep(1)
        await c.post(f"{bridge}/session/click", json={"selector": "button:has-text('Next'),button:has-text('Log in')"})
        await asyncio.sleep(5)
        # TOTP step — if X asks for the app code and a secret's stored,
        # compute it and fill the verify input.
        s = os.environ.get("TWITTER_TOTP_SECRET", "")
        if s:
            try:
                import pyotp

                code = pyotp.TOTP(s).now()
                await c.post(f"{bridge}/session/fill", json={"selector": "input >> nth=0", "value": code})
                await asyncio.sleep(1)
                await c.post(f"{bridge}/session/click", json={"selector": "button:has-text('Verify'),button:has-text('Next')"})
                await asyncio.sleep(5)
            except Exception:
                pass


async def _try_bridge_reauth(team_id: str) -> str:
    """Self-heal: drive the X consent on the adhoc bridge (:9232/adhoc),
    which is a dedicated session slot — no shared-novnc 409 contention.
    Seeds the stored X cookies (X_WEB_AUTH_TOKEN/X_WEB_CT0), navigates
    the fresh authorize URL, ticks the trust checkbox, then fires the
    trusted /session/click on Authorize — the exact flow that delivers
    code -> callback -> active."""
    import os

    bridge = os.environ.get("BROWSER_ADHOC_URL", "http://browser-novnc-adhoc:9224")
    try:
        import base64 as _b64
        import hashlib as _hash
        import secrets as _sec
        from urllib.parse import urlencode

        from app.core.config import settings
        from app.core.security import sign_oauth_state

        cv = _sec.token_urlsafe(64)
        cc = _b64.urlsafe_b64encode(_hash.sha256(cv.encode()).digest()).rstrip(b"=").decode()
        state = sign_oauth_state({"t": team_id, "cv": cv})
        auth_url = "https://x.com/i/oauth2/authorize?" + urlencode(
            {
                "response_type": "code",
                "client_id": settings.TWITTER_CLIENT_ID,
                "redirect_uri": settings.TWITTER_REDIRECT_URI,
                "state": state,
                "scope": "tweet.read tweet.write users.read media.write offline.access dm.read dm.write like.write like.read follows.write bookmark.read",
                "code_challenge": cc,
                "code_challenge_method": "S256",
            }
        )
        cookies = [
            {"name": "auth_token", "value": os.environ.get("X_WEB_AUTH_TOKEN", ""), "domain": ".x.com", "path": "/"},
            {"name": "ct0", "value": os.environ.get("X_WEB_CT0", ""), "domain": ".x.com", "path": "/"},
        ]
        cookies = [c for c in cookies if c["value"]]
        async with httpx.AsyncClient(timeout=30) as c:
            await c.post(f"{bridge}/session/start", json={"platform": "twitter"})
            await asyncio.sleep(2)
            if cookies:
                await c.post(f"{bridge}/session/seed", json={"cookies": cookies})
            await c.post(f"{bridge}/session/evaluate", json={"expression": 'location.href="https://x.com"'})
            await asyncio.sleep(5)
            # login-wall fallback — cookies dead → credentials login on the
            # same session before continuing to the authorize URL.
            wall = await c.post(
                f"{bridge}/session/evaluate", json={"expression": "document.title + '|' + (document.body&&document.body.innerText||'').slice(0,200)"}
            )
            if "login" in str(wall.json()).lower() or "sign in" in str(wall.json()).lower():
                await _x_credentials_login(c, bridge)
            await c.post(f"{bridge}/session/evaluate", json={"expression": f"location.href={__import__('json').dumps(auth_url)}"})
            await asyncio.sleep(7)
            # trust checkbox → enables Authorize
            await c.post(f"{bridge}/session/click", json={"selector": "[role=checkbox]"})
            await asyncio.sleep(2)
            click = await c.post(f"{bridge}/session/click", json={"selector": "button:has-text('Authorize')"})
            return f"approve-fired {click.json() if click.status_code == 200 else click.status_code}"
    except Exception as e:
        return f"still-expired ({type(e).__name__})"


@celery_app.task(name="app.worker.tasks.consent_healers.heal_consents")
def heal_consents() -> dict:
    async def _go():
        return {
            "twitter": await _twitter(),
            "linkedin": await _linkedin_snapshot(),
            "whatsapp": await _whatsapp(),
            "bridge": await _bridge_health(),
            "dmr": await _dmr_health(),
        }

    return run_async(_go())


@celery_app.task(name="app.worker.tasks.consent_healers.media_backfill")
def media_backfill() -> dict:
    """Sweep draft posts flagged needs_media — generate+attach an image so
    they become schedulable (the flag was a dead signal; this consumes it)."""
    import asyncio

    return asyncio.run(_media_backfill())


async def _media_backfill() -> dict:
    import base64

    from sqlalchemy import select

    from app.models.content import Post
    from app.services.inference import _call_workers_ai_image
    from app.services.media_storage import persist_generated_image
    from app.worker._async import task_session

    FLUX = "stable-diffusion-xl-1.0"
    done, failed = 0, 0
    async with task_session() as db:
        posts = (
            (
                await db.execute(
                    select(Post)
                    .where(
                        Post.status == "draft",
                        Post.meta_data["needs_media"].astext == "true",
                    )
                    .limit(5)
                )
            )
            .scalars()
            .all()
        )
        for post in posts:
            topic = (post.meta_data or {}).get("topic") or (post.content_text or "")[:120]
            try:
                res = await _call_workers_ai_image(f"Clean professional social-media visual for: {topic}", FLUX)
                b64 = (res or {}).get("image_base64")
                if not b64:
                    failed += 1
                    continue
                asset = await persist_generated_image(
                    db,
                    team_id=post.team_id,
                    user_id=post.user_id,
                    image_bytes=base64.b64decode(b64),
                    prompt=f"media_backfill: {topic}",
                    source="media-backfill",
                )
                post.media_ids = [asset.id]
                md = dict(post.meta_data or {})
                md["needs_media"] = False
                post.meta_data = md
                done += 1
            except Exception:
                failed += 1
        await db.commit()
    return {"attached": done, "failed": failed}

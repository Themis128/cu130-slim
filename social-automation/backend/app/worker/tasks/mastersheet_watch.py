"""mastersheet_watch — poll the Founders' Circle Notion mastersheet for changes.

Renders the JS page on the adhoc browser bridge, extracts the content
(weeks/recordings/slides/homework), diffs against the last persisted snapshot,
Telegram-notifies on change, and logs the new data as datalake events
(coaching_action) so the insights loop adopts it.
"""

import asyncio
import difflib
import hashlib
import logging
import re
import time
from collections import Counter
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from app.core.security import decrypt_token
from app.models import AnalyticsEvent, SocialAccount
from app.services.telegram_api import TelegramAPIClient
from app.worker._async import run_async, task_session
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)

BRIDGE = "http://browser-novnc-adhoc:9224"
SHEET_URL = "https://sedate-ulna-278.notion.site/Learners-Founders-Circle-Mastersheet-3ff172ca8fc080b0ac85e0ea6b2673e9"
TELEGRAM_CHAT = "-1004436942504"
EVENT_TYPE = "mastersheet_snapshot"
PLATFORM = "founders_circle"


def _bridge(path, data, tries=3):
    for i in range(tries):
        try:
            r = httpx.post(f"{BRIDGE}{path}", json=data, timeout=45)
            if r.status_code == 409 or (r.status_code == 500 and "context was destroyed" in r.text):
                time.sleep(15)
                continue
            r.raise_for_status()
            return r.json()
        except httpx.HTTPStatusError:
            if i == tries - 1:
                raise
    return {}


def _ev(expr):
    return _bridge("/session/evaluate", {"expression": expr}).get("result")


def _fetch() -> dict:
    try:
        _bridge("/session/close", {})
    except Exception:
        pass
    _bridge("/session/start", {"platform": "reddit", "interactive": False, "force": True})
    time.sleep(4)
    _bridge("/session/navigate", {"url": SHEET_URL})
    for _ in range(8):
        if _ev("document.body.innerText.includes('Mastersheet')"):
            break
        time.sleep(4)
    else:
        raise RuntimeError("mastersheet never rendered")
    time.sleep(4)  # let the table lazy-load
    text = _ev("document.body.innerText") or ""
    return {"text": text[:8000], "hash": hashlib.sha256(text.encode()).hexdigest()}


# ── diff analytics ──────────────────────────────────────────────────────────

_STOP = frozenset(
    "the a an and or to of in for on your you we they it is are be this that with".split()
    + "skip content notion uses cookies see cookie notice details accept reject get free".split()
)

_PATTERNS = [
    ("new_week", re.compile(r"week\s*\d+", re.I)),
    ("new_homework", re.compile(r"homework|must-do|action", re.I)),
    ("new_recording", re.compile(r"youtu\.be|zoom\.us|recording", re.I)),
    ("new_slides", re.compile(r"canva\.link|slides|presentation", re.I)),
]


def _keywords(lines: list[str], n: int = 5) -> list[str]:
    """TF-score the new lines — salient terms the alert should name."""
    words = Counter(w.lower() for line in lines for w in re.findall(r"[A-Za-zΑ-ω]{4,}", line) if w.lower() not in _STOP)
    return [w for w, _ in words.most_common(n)]


def _classify_delta(old_text: str, new_text: str) -> dict:
    """difflib line-delta → {changed, kind[], added_lines[], keywords[]}."""
    if not old_text:
        return {"changed": True, "kind": ["first_snapshot"], "added": [], "keywords": []}
    delta = [
        line.lstrip("+ ").strip()
        for line in difflib.unified_diff(old_text.splitlines(), new_text.splitlines(), lineterm="", n=0)
        if line.startswith("+") and not line.startswith("+++")
    ]
    added = [line for line in delta if len(line) > 2][:50]
    kinds = sorted({k for k, pat in _PATTERNS if any(pat.search(line) for line in added)})
    return {
        "changed": bool(added),
        "kind": kinds or (["edit"] if added else []),
        "added": added[:20],
        "keywords": _keywords(added),
    }


async def _notify(db, text: str) -> None:
    acc = (await db.execute(select(SocialAccount).where(SocialAccount.platform == "telegram", SocialAccount.status == "active").limit(1))).scalar_one()
    enc = acc.meta_data.get("bot_token_enc")
    tok = decrypt_token(enc.encode() if isinstance(enc, str) else enc) if enc else decrypt_token(acc.access_token_enc)
    await TelegramAPIClient(tok).send_message(TELEGRAM_CHAT, text)


@celery_app.task(name="app.worker.tasks.mastersheet_watch.watch_mastersheet")
def watch_mastersheet() -> dict:
    return run_async(_run())


async def _run() -> dict:
    snap = await asyncio.to_thread(_fetch)
    async with task_session() as db:
        last = (
            await db.execute(select(AnalyticsEvent).where(AnalyticsEvent.event_type == EVENT_TYPE).order_by(AnalyticsEvent.occurred_at.desc()).limit(1))
        ).scalar_one_or_none()
        acc = (await db.execute(select(SocialAccount).where(SocialAccount.platform == "telegram", SocialAccount.status == "active").limit(1))).scalar_one()
        team_id = last.team_id if last else acc.team_id
        delta = _classify_delta((last.meta_data.get("text", "") if last else ""), snap["text"])
        changed = delta["changed"]
        db.add(
            AnalyticsEvent(
                team_id=team_id,
                event_type=EVENT_TYPE,
                platform=PLATFORM,
                occurred_at=datetime.now(UTC),
                meta_data={"hash": snap["hash"], "delta": delta, "text": snap["text"]},
            )
        )
        if changed:
            kind_lbl = " + ".join(delta["kind"]) or "update"
            kw = ", ".join(delta["keywords"]) or "—"
            head = "\n".join(delta["added"][:6])
            db.add(
                AnalyticsEvent(
                    team_id=team_id,
                    event_type="coaching_action",
                    platform=PLATFORM,
                    occurred_at=datetime.now(UTC),
                    meta_data={
                        "source": "mastersheet_watch",
                        "topic": f"mastersheet: {kind_lbl}",
                        "kind": delta["kind"],
                        "keywords": delta["keywords"],
                        "added_lines": delta["added"],
                    },
                )
            )
            await _notify(db, f"📚 Founders' Circle — {kind_lbl}\nkeywords: {kw}\n\n{head[:700]}")
            snap["telegram_notified"] = True
        await db.commit()
        snap["telegram_notified"] = snap.get("telegram_notified", False)
    return {"changed": changed, "hash": snap["hash"][:12], "delta": delta["kind"]}

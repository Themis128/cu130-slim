"""Edit-diff learning — the kept-vs-drafted delta as a voice signal.

Adopted from the self-improving-skill pattern (sylph): every post a human
edits before approving is a training signal. ``update_post`` snapshots the
generated baseline into ``post.meta_data.original_text`` on first edit;
this sweep diffs baseline vs kept copy and distills repeated corrections
into voice suggestions.

Proposals only — suggestions land on audit_logs + Slack for review (the
human stays the approval gate); nothing mutates ``voice_signature``
silently.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select

from app.models.content import Post
from app.models.user import AuditLog

logger = logging.getLogger(__name__)

_WINDOW = timedelta(days=30)
# A correction pattern only becomes a suggestion when it repeats: >=2 posts
# AND >=40% of the week's edited posts — single edits are taste, not signal.
_MIN_PATTERN_POSTS = 2
_PATTERN_SHARE = 0.4

_EMOJI_RE = re.compile(r"[\U0001f300-\U0001faff☀-➿]")
_HASHTAG_RE = re.compile(r"#\w+")
_QUESTION_RE = re.compile(r"\?")


@dataclass
class EditDiff:
    post_id: str
    features: dict[str, bool]
    length_delta: int


def _first_line(text: str) -> str:
    return next((ln.strip() for ln in (text or "").splitlines() if ln.strip()), "")


def diff_features(original: str, edited: str) -> dict[str, bool]:
    """Deterministic kept-vs-drafted features — no LLM, fully auditable."""
    orig, edit = original or "", edited or ""
    orig_tags, edit_tags = set(_HASHTAG_RE.findall(orig)), set(_HASHTAG_RE.findall(edit))
    orig_tail, edit_tail = orig[-300:], edit[-300:]
    return {
        "hook_rewritten": _first_line(orig) != _first_line(edit),
        "shortened": len(edit) < 0.85 * len(orig) if orig else False,
        "lengthened": len(edit) > 1.15 * len(orig) if orig else True,
        "emoji_removed": len(_EMOJI_RE.findall(edit)) < len(_EMOJI_RE.findall(orig)),
        "emoji_added": len(_EMOJI_RE.findall(edit)) > len(_EMOJI_RE.findall(orig)),
        "hashtags_changed": orig_tags != edit_tags,
        "cta_added": bool(_QUESTION_RE.search(edit_tail)) and not _QUESTION_RE.search(orig_tail),
        "cta_removed": bool(_QUESTION_RE.search(orig_tail)) and not _QUESTION_RE.search(edit_tail),
        "paragraphs_added": edit.count("\n\n") > orig.count("\n\n"),
    }


_SUGGESTIONS = {
    "hook_rewritten": "Hook rewrites recur — strengthen the first-line hook: name a situation the reader recognizes, ≤12 words",
    "shortened": "Edits keep shortening drafts — tighten generation length; cut filler lines before the CTA",
    "lengthened": "Edits keep adding detail — drafts under-deliver on specifics; include one concrete number/outcome",
    "emoji_removed": "Edits strip emoji — the voice reads emoji as decoration; keep at most one, or none",
    "emoji_added": "Edits add emoji — the voice wants a lighter register than drafts produce",
    "hashtags_changed": "Edits keep retagging — align hashtag selection with what the human actually keeps",
    "cta_added": "Edits add a closing question/CTA — drafts end flat; always close with a specific question",
    "cta_removed": "Edits remove the CTA question — drafted CTAs read as engagement-bait; drop or soften them",
    "paragraphs_added": "Edits add paragraph breaks — drafts arrive as walls of text; first line = hook, then breathing room",
}


async def collect_edit_diffs(db, team_id, window: timedelta = _WINDOW) -> list[EditDiff]:
    """Posts whose kept copy differs from the snapshotted baseline, in window."""
    q = select(Post.id, Post.content_text, Post.meta_data).where(
        Post.team_id == team_id,
        Post.updated_at >= datetime.now(UTC) - window,
        Post.meta_data["original_text"].isnot(None),
    )
    diffs: list[EditDiff] = []
    for post_id, content, meta in (await db.execute(q)).all():
        original = (meta or {}).get("original_text") or ""
        edited = content or ""
        if not original.strip() or original.strip() == edited.strip():
            continue  # no usable baseline, or the edit never landed
        diffs.append(
            EditDiff(str(post_id), diff_features(original, edited), len(edited) - len(original))
        )
    return diffs


def distill_suggestions(diffs: list[EditDiff]) -> list[str]:
    """Recurring correction patterns → voice suggestions (proposals)."""
    if not diffs:
        return []
    counts = Counter(f for d in diffs for f, on in d.features.items() if on)
    threshold = max(_MIN_PATTERN_POSTS, int(len(diffs) * _PATTERN_SHARE + 0.999))
    return [_SUGGESTIONS[f] for f, n in counts.most_common() if n >= threshold]


async def run_edit_diff_sweep(
    db,
    team_id,
    *,
    window: timedelta = _WINDOW,
    post_slack: bool = True,
) -> dict[str, Any]:
    """Weekly edit-diff analysis → suggestions on audit_logs + Slack."""
    diffs = await collect_edit_diffs(db, team_id, window)
    suggestions = distill_suggestions(diffs)
    feature_counts = dict(Counter(f for d in diffs for f, on in d.features.items() if on))
    report: dict[str, Any] = {
        "edited_posts": len(diffs),
        "features": feature_counts,
        "suggestions": suggestions,
    }

    db.add(AuditLog(
        team_id=team_id,
        action="edit-diff",
        resource_type="sweep",
        detail=f"edited={len(diffs)} suggestions={len(suggestions)}",
        meta={"features": feature_counts, "suggestions": suggestions},
    ))

    if post_slack and suggestions:
        try:
            from app.services.slack_notifications import post_alert_to_slack

            lines = [
                f"*Edit-diff voice learning — {len(diffs)} edited posts, {len(suggestions)} suggestions*",
                *[f"• {s}" for s in suggestions],
            ]
            await post_alert_to_slack("\n".join(lines))
        except Exception:  # noqa: BLE001 — suggestions already persisted
            logger.exception("edit-diff: slack post failed")

    return report

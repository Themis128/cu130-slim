"""Content-pillar helpers — coverage rotation and heuristic classification.

Pillars implement the team's 3P content strategy (Proof / Testing /
Perspective). Two jobs live here:

- ``least_covered_pillar`` — pick the pillar with the fewest posts in the
  recent window (the "auto" rotation used by generation + post creation).
- ``classify_pillar`` — keyword heuristic mapping post text to a pillar.
  Used to backfill ``Post.pillar_id`` for posts created before attribution
  existed. Intentionally simple and deterministic — a wrong guess here only
  skews the coverage report, never the content itself.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content import Pillar, Post

# Ordered by pillar semantics — first strong match wins. Keep patterns
# lowercase; matching is case-insensitive.
_PILLAR_HINTS: dict[str, list[str]] = {
    # What we're doing — experiments, numbers, before/after, incidents.
    "testing": [
        r"\bi tried\b", r"\bi tested\b", r"\bexperiment", r"\bresults?\b",
        r"\bhonest log\b", r"\bbroke\b", r"\bincident\b", r"\bbefore/after\b",
        r"\bmeasured\b", r"\bbenchmark", r"\bsaved\b", r"\bcost\b", r"€\d",
        r"\$\d", r"\d+%", r"\bthis week\b", r"\bday \d+\b", r"\bbuild log\b",
        r"\bpostmortem\b", r"\bwhat happened\b", r"\bran for\b",
    ],
    # What we know — how-tos, frameworks, step-by-step, playbooks.
    "proof": [
        r"\bhow to\b", r"\bhere'?s how\b", r"\bstep.?by.?step\b",
        r"\bframework\b", r"\bplaybook\b", r"\btutorial\b", r"\bguide\b",
        r"\bchecklist\b", r"\bpattern[s]?\b", r"\brecipe\b",
        r"\bthe stack\b", r"\bwalkthrough\b", r"\bmigrat", r"\bapproach\b",
    ],
    # What we believe — opinions, standards, contrarian takes.
    "perspective": [
        r"\bmost people\b", r"\byou don'?t need\b", r"\bi believe\b",
        r"\bactually means\b", r"\bshouldn'?t\b", r"\bstop \b",
        r"\bunpopular\b", r"\bmyth\b", r"\bwhat matters\b", r"\brule\b",
        r"\bno lock.?in\b", r"\bdiscipline\b", r"\bnot a limitation\b",
        r"\bthe point\b", r"\btrap\b",
    ],
}

_COMPILED = {
    pillar: [re.compile(p, re.IGNORECASE) for p in pats]
    for pillar, pats in _PILLAR_HINTS.items()
}


def classify_pillar(text: str, pillars: list[Pillar]) -> Pillar | None:
    """Map post text to a pillar via keyword heuristics.

    Matches on lower-cased pillar names — a pillar named "Testing" keys off
    the "testing" hint list. Unknown pillar names get no hints and simply
    never match. Returns None when nothing matches.
    """
    if not text:
        return None
    scores: dict[UUID, int] = {}
    by_name = {p.name.strip().lower(): p for p in pillars}
    for name, patterns in _COMPILED.items():
        pillar = by_name.get(name)
        if pillar is None:
            continue
        scores[pillar.id] = sum(1 for pat in patterns if pat.search(text))
    best = max(scores.values(), default=0)
    if best == 0:
        return None
    winner_id = min(pid for pid, s in scores.items() if s == best)
    return next(p for p in pillars if p.id == winner_id)


async def least_covered_pillar(
    db: AsyncSession,
    team_id: UUID,
    *,
    days: int = 14,
) -> Pillar | None:
    """The pillar with the fewest posts in the recent window ("auto" pick).

    Ties break on ``sort_order`` so the rotation is stable.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    counts = (
        await db.execute(
            select(Pillar, func.count(Post.id))
            .outerjoin(
                Post,
                (Post.pillar_id == Pillar.id) & (Post.created_at >= since),
            )
            .where(Pillar.team_id == team_id)
            .group_by(Pillar.id)
            .order_by(func.count(Post.id), Pillar.sort_order)
        )
    ).all()
    return counts[0][0] if counts else None


async def resolve_pillar(
    db: AsyncSession,
    team_id: UUID,
    pillar: str | None,
    *,
    auto_days: int = 14,
) -> Pillar | None:
    """Resolve a caller-supplied pillar reference to a ``Pillar`` row.

    ``"auto"`` → least-covered pillar; a UUID or case-insensitive name → that
    pillar; ``None``/unresolvable → ``None``.
    """
    if not pillar:
        return None
    if pillar.strip().lower() == "auto":
        return await least_covered_pillar(db, team_id, days=auto_days)
    import uuid as _uuid

    try:
        cond = Pillar.id == _uuid.UUID(pillar)
    except ValueError:
        cond = func.lower(Pillar.name) == pillar.strip().lower()
    return (
        await db.execute(select(Pillar).where(Pillar.team_id == team_id, cond))
    ).scalars().first()

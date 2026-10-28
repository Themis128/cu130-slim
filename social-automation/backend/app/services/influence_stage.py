"""Influencer-entrepreneurship staged progression — Cheng, de Regt & Young
(Psychology and Marketing, 2024, doi:10.1002/mar.22084).

Five stages: potential → formulation → competence → monetization → reward.
Deterministic classifier mapping observable signals (followers, engagement,
commercial presence) to the current stage and the stage-appropriate action
set — used for (a) internal Visibility-Era journey gating and (b) agency
growth audits for partner/client brands. Thresholds are heuristics we own;
the stage semantics come from the paper.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    key: str
    name: str
    attentional_capital: str  # what the influencer controls here
    brand_relation: str  # paper's guidance on brand engagement
    actions: tuple[str, ...]  # stage-appropriate playbook


STAGES: tuple[Stage, ...] = (
    Stage(
        key="potential",
        name="Influence potential",
        attentional_capital="none — aspiring creator, audience undefined",
        brand_relation="brands should not pitch — nurture only",
        actions=(
            "define niche + ideal-follower persona",
            "complete profile: photo, bio, CTA, pinned credibility",
            "consistent posting cadence (>=3x/week) on ONE platform",
        ),
    ),
    Stage(
        key="formulation",
        name="Influence formulation",
        attentional_capital="small engaged nucleus forming",
        brand_relation="co-creation / gifted collabs only — no paid asks yet",
        actions=(
            "content-market fit: double down on formats that earn saves+shares",
            "platform focus stays single; adapt for second platform only after fit",
            "reply to every comment; convert followers to community",
        ),
    ),
    Stage(
        key="competence",
        name="Influence competence",
        attentional_capital="attentional capital proven — repeatable reach",
        brand_relation="short-term paid collabs viable; vet fit carefully",
        actions=(
            "amplify proven posts (boost/capped ads) — the 'walk' gate",
            "media kit + rate card drafted; disclosure discipline (#ad)",
            "track engagement rate, not vanity followers",
        ),
    ),
    Stage(
        key="monetization",
        name="Influence monetization",
        attentional_capital="persuasive power converts to revenue",
        brand_relation="long-term partnerships; negotiate equity/retainers",
        actions=(
            "recurring offers: retainers, affiliate, sponsored series",
            "diversify revenue — never one brand >30% of income",
            "CRM-link deals to measure real revenue, not just reach",
        ),
    ),
    Stage(
        key="reward",
        name="Influence reward",
        attentional_capital="human brand → enterprise; follower-to-founder",
        brand_relation="the influencer IS a brand — peer-level, sometimes competitor",
        actions=(
            "own products/services on the audience's trust (Polar, courses)",
            "brand equity defense: audits, authenticity maintenance",
            "reinvest into the machine that built the stage",
        ),
    ),
)

_STAGE_NAMES = {s.key: s for s in STAGES}


def classify_stage(
    *,
    followers: int | None,
    engagement_rate: float | None = None,
    paid_collabs: int = 0,
    recurring_offers: int = 0,
    own_products: bool = False,
) -> Stage:
    """Map observable signals to the current stage (first matching wins,
    evaluated top-down from reward → potential)."""
    if own_products:
        return _STAGE_NAMES["reward"]
    if recurring_offers >= 1 or paid_collabs >= 3:
        return _STAGE_NAMES["monetization"]
    followers = followers or 0
    if followers >= 10_000 or paid_collabs >= 1 or (engagement_rate or 0) >= 0.06:
        return _STAGE_NAMES["competence"]
    if followers >= 1_000 or (engagement_rate or 0) >= 0.03:
        return _STAGE_NAMES["formulation"]
    return _STAGE_NAMES["potential"]

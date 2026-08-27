"""Duplicate content detector for carousel / social copy."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from difflib import SequenceMatcher

_STOPWORDS = frozenset(
    """
    a an the and or but if then so to for of in on at by with without from as is are was were be been being
    you your we our they their it its this that these those not no more most than into over after before
    can could will would should may might must do does did done get got make made help helps just also
    """.split()
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def normalize_text(text: str) -> str:
    return " ".join(_WORD_RE.findall((text or "").lower()))


def content_tokens(text: str) -> list[str]:
    return [w for w in normalize_text(text).split() if w not in _STOPWORDS and len(w) > 2]


def stem_token(word: str) -> str:
    """Lightweight stemmer for duplicate matching (servers→server, worries→worry)."""
    w = word.lower()
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"  # worries → worry
    for suffix in ("ing", "ers", "er", "ly", "ed", "es", "s"):
        if len(w) > len(suffix) + 3 and w.endswith(suffix):
            return w[: -len(suffix)]
    return w


def content_stems(text: str) -> set[str]:
    return {stem_token(w) for w in content_tokens(text)}


@dataclass
class DuplicateHit:
    left_field: str
    right_field: str
    score: float
    reason: str
    left_snippet: str = ""
    right_snippet: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DuplicateReport:
    has_duplicates: bool
    hits: list[DuplicateHit] = field(default_factory=list)
    resolved: bool = False
    actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "has_duplicates": self.has_duplicates,
            "resolved": self.resolved,
            "actions": self.actions,
            "hits": [h.to_dict() for h in self.hits],
        }


def similarity_score(a: str, b: str) -> float:
    """0..1 similarity using containment, token Jaccard, stems, and sequence ratio."""
    na, nb = normalize_text(a), normalize_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    if na in nb or nb in na:
        shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
        return max(0.72, len(shorter) / max(len(longer), 1))

    wa, wb = set(na.split()), set(nb.split())
    jaccard = len(wa & wb) / max(len(wa | wb), 1)

    sa, sb = content_stems(a), content_stems(b)
    stem_j = len(sa & sb) / max(len(sa | sb), 1) if sa and sb else 0.0
    # If the shorter phrase's key stems are mostly covered by the longer one,
    # treat as near-duplicate (e.g. "server worries" vs "don't worry about servers").
    if sa and sb:
        stem_shorter, stem_longer = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
        shared = stem_shorter & stem_longer
        coverage = len(shared) / max(len(stem_shorter), 1)
        if len(shared) >= 2 and coverage >= 0.66:
            stem_j = max(stem_j, 0.52 + 0.35 * coverage)

    seq = SequenceMatcher(None, na, nb).ratio()

    return max(jaccard * 0.9, stem_j, seq, (jaccard + stem_j + seq) / 3)


def is_duplicate(a: str, b: str, *, threshold: float = 0.48) -> bool:
    if not (a or "").strip() or not (b or "").strip():
        return False
    return similarity_score(a, b) >= threshold


def detect_field_duplicates(
    fields: dict[str, str],
    *,
    prefix: str = "",
    threshold: float = 0.48,
) -> list[DuplicateHit]:
    """Compare every pair of non-empty fields."""
    hits: list[DuplicateHit] = []
    items = [(k, (v or "").strip()) for k, v in fields.items() if (v or "").strip()]
    for i, (lk, lv) in enumerate(items):
        for rk, rv in items[i + 1 :]:
            score = similarity_score(lv, rv)
            if score >= threshold:
                hits.append(
                    DuplicateHit(
                        left_field=f"{prefix}{lk}" if prefix else lk,
                        right_field=f"{prefix}{rk}" if prefix else rk,
                        score=round(score, 3),
                        reason="near_duplicate_text",
                        left_snippet=lv[:120],
                        right_snippet=rv[:120],
                    )
                )
    return hits


def detect_slide_duplicates(slide: dict, *, index: int | None = None) -> list[DuplicateHit]:
    prefix = f"slide[{index}]." if index is not None else ""
    return detect_field_duplicates(
        {
            "title": slide.get("title") or "",
            "body": slide.get("body") or "",
            "highlight": slide.get("highlight") or "",
        },
        prefix=prefix,
        threshold=0.46,
    )


def detect_cross_slide_duplicates(slides: list[dict], *, threshold: float = 0.62) -> list[DuplicateHit]:
    """Flag slides that repeat nearly the same title or body."""
    hits: list[DuplicateHit] = []
    for i, left in enumerate(slides):
        for j in range(i + 1, len(slides)):
            right = slides[j]
            for key in ("title", "body"):
                lv = (left.get(key) or "").strip()
                rv = (right.get(key) or "").strip()
                if not lv or not rv:
                    continue
                score = similarity_score(lv, rv)
                if score >= threshold:
                    hits.append(
                        DuplicateHit(
                            left_field=f"slide[{i}].{key}",
                            right_field=f"slide[{j}].{key}",
                            score=round(score, 3),
                            reason="cross_slide_duplicate",
                            left_snippet=lv[:120],
                            right_snippet=rv[:120],
                        )
                    )
    return hits


def detect_caption_slide_duplicates(slides: list[dict], caption: str) -> list[DuplicateHit]:
    hits: list[DuplicateHit] = []
    cap = (caption or "").strip()
    if not cap:
        return hits
    for i, slide in enumerate(slides):
        for key in ("title", "body"):
            val = (slide.get(key) or "").strip()
            if val and is_duplicate(cap, val, threshold=0.55):
                hits.append(
                    DuplicateHit(
                        left_field="caption",
                        right_field=f"slide[{i}].{key}",
                        score=round(similarity_score(cap, val), 3),
                        reason="caption_repeats_slide",
                        left_snippet=cap[:120],
                        right_snippet=val[:120],
                    )
                )
    return hits


def detect_carousel_duplicates(slides: list[dict], caption: str = "") -> DuplicateReport:
    hits: list[DuplicateHit] = []
    for i, slide in enumerate(slides):
        hits.extend(detect_slide_duplicates(slide, index=i))
    hits.extend(detect_cross_slide_duplicates(slides))
    hits.extend(detect_caption_slide_duplicates(slides, caption))
    return DuplicateReport(has_duplicates=bool(hits), hits=hits)


def _pick_primary(*texts: str) -> str:
    candidates = [t.strip() for t in texts if (t or "").strip()]
    if not candidates:
        return ""
    # Prefer the longest substantive NLP line.
    return max(candidates, key=lambda t: (len(content_tokens(t)), len(t)))


def resolve_slide_duplicates(slide: dict) -> tuple[dict, list[str]]:
    """Collapse duplicate title/body/highlight down to non-repeating NLP fields."""
    out = dict(slide)
    actions: list[str] = []
    title = (out.get("title") or "").strip()
    body = (out.get("body") or "").strip()
    highlight = (out.get("highlight") or "").strip() or None

    # Drop highlight if it duplicates title or body.
    if highlight and (
        is_duplicate(highlight, title)
        or is_duplicate(highlight, body)
    ):
        actions.append("cleared_duplicate_highlight")
        highlight = None

    if title and body and is_duplicate(title, body):
        primary = _pick_primary(title, body)
        # Short distinct headline vs longer body: only keep both if not duplicate.
        title, body = primary, ""
        actions.append("collapsed_title_body_to_single_nlp_line")
    elif not title and body:
        title, body = body, ""
        actions.append("promoted_body_to_title")

    # If highlight remains and title empty, promote highlight once.
    if not title and highlight:
        title, highlight = highlight, None
        actions.append("promoted_highlight_to_title")

    out["title"] = title
    out["body"] = body
    out["highlight"] = highlight
    return out, actions


def resolve_cross_slide_duplicates(slides: list[dict]) -> tuple[list[dict], list[str]]:
    """If two slides share nearly identical titles/bodies, clear the later duplicate body/title soft-fix."""
    out = [dict(s) for s in slides]
    actions: list[str] = []
    for i, left in enumerate(out):
        for j in range(i + 1, len(out)):
            right = out[j]
            for key in ("title", "body"):
                lv = (left.get(key) or "").strip()
                rv = (right.get(key) or "").strip()
                if lv and rv and is_duplicate(lv, rv, threshold=0.62):
                    # Keep earlier slide; blank the later duplicate field.
                    if key == "body":
                        right["body"] = ""
                        actions.append(f"cleared_cross_slide_body slide[{j}]~slide[{i}]")
                    else:
                        # Differentiate later title lightly if body still unique.
                        if (right.get("body") or "").strip() and not is_duplicate(
                            right.get("body") or "", lv, threshold=0.55
                        ):
                            # Keep later body, shorten title to avoid twin headlines.
                            words = content_tokens(right.get("body") or "")[:5]
                            right["title"] = " ".join(words).capitalize() if words else right["title"]
                            actions.append(f"rewrote_cross_slide_title slide[{j}]")
                        else:
                            # Both redundant — clear later body and keep title as-is once.
                            right["body"] = ""
                            actions.append(f"cleared_cross_slide_duplicate_body slide[{j}]")
            out[j] = right
    return out, actions


def resolve_carousel_duplicates(slides: list[dict], caption: str) -> tuple[list[dict], str, DuplicateReport]:
    """Detect and resolve duplicate carousel content. Prefer a single NLP line per idea."""
    report = detect_carousel_duplicates(slides, caption)
    actions: list[str] = []
    cleaned: list[dict] = []
    for slide in slides:
        fixed, slide_actions = resolve_slide_duplicates(slide)
        cleaned.append(fixed)
        actions.extend(slide_actions)

    cleaned, cross_actions = resolve_cross_slide_duplicates(cleaned)
    actions.extend(cross_actions)

    # Caption: if it largely repeats slide 0 title, keep caption NLP only (already one string).
    cleaned_caption = (caption or "").strip()
    report.actions = actions
    report.resolved = bool(actions) or not report.has_duplicates
    # Re-detect after resolve for residual hits.
    residual = detect_carousel_duplicates(cleaned, cleaned_caption)
    report.has_duplicates = residual.has_duplicates
    report.hits = residual.hits if residual.hits else report.hits
    if actions and not residual.has_duplicates:
        report.has_duplicates = False
        report.hits = []
    return cleaned, cleaned_caption, report

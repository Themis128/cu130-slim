"""NLP plain-English checker and fixer for social / carousel copy."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field

from fastapi import HTTPException

# Injected into LLM prompts for content / carousel generation.
PLAIN_ENGLISH_RULES = """
PLAIN ENGLISH RULES (must follow):
- Use short, everyday words. Write so a busy non-expert can understand on first read.
- Prefer common words over jargon. If a technical term is required, explain it in plain words.
- Avoid buzzwords and filler (e.g. "leverage", "synergy", "robust", "seamless", "cutting-edge",
  "enterprise-grade", "holistic", "paradigm", "unlock", "empower", "next-gen", "scalable solutions").
- Prefer: "help", "simple", "fast", "clear", "works well", "no long contracts", "easy to use".
- Keep sentences short (aim under 20 words). One idea per sentence.
- Titles: concrete and human (not vague marketing slogans).
- Bodies: say what you do and why it helps — no fluff.
""".strip()

_JARGON_PATTERN = re.compile(
    r"\b("
    r"leverage|synerg(?:y|ies)|robust|seamless|cutting[- ]edge|enterprise[- ]grade|"
    r"holistic|paradigm|unlock(?:s|ing)?|empower(?:s|ing|ment)?|next[- ]gen(?:eration)?|"
    r"scalable\s+solutions?|disrupt(?:ive|ion)?|optimize|optimisation|utilize|utilise|"
    r"best[- ]in[- ]class|world[- ]class|transformative|innovation|ecosystem|"
    r"value\s+proposition|go[- ]to[- ]market|mission[- ]critical|end[- ]to[- ]end|"
    r"frictionless|hyper[- ]?scale|cloud[- ]native\s+excellence|digital\s+transformation|"
    r"thought[- ]leadership|actionable\s+insights|streamline|orchestrat(?:e|ion)|"
    r"simplifying\s+enterprise[- ]grade|expert\s+solutions?"
    r")\b",
    re.IGNORECASE,
)


@dataclass
class NlpIssue:
    field: str
    reason: str
    snippet: str
    matches: list[str] = dc_field(default_factory=list)


@dataclass
class NlpCheckReport:
    needs_fix: bool
    issues: list[NlpIssue] = dc_field(default_factory=list)
    fixed: bool = False
    fields_rewritten: list[str] = dc_field(default_factory=list)
    duplicates: dict = dc_field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "needs_fix": self.needs_fix,
            "fixed": self.fixed,
            "fields_rewritten": self.fields_rewritten,
            "issues": [asdict(i) for i in self.issues],
            "duplicates": self.duplicates,
        }


def _avg_sentence_words(text: str) -> float:
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    if not sentences:
        return 0.0
    return sum(len(s.split()) for s in sentences) / len(sentences)


def check_plain_english(text: str, field: str = "text") -> list[NlpIssue]:
    """Inspect one string and return vocabulary / clarity issues."""
    issues: list[NlpIssue] = []
    if not text or not text.strip():
        return issues

    matches = sorted({m.group(0).lower() for m in _JARGON_PATTERN.finditer(text)})
    if matches:
        issues.append(
            NlpIssue(
                field=field,
                reason="jargon_or_buzzwords",
                snippet=text[:160],
                matches=matches,
            )
        )

    avg = _avg_sentence_words(text)
    if avg > 22:
        issues.append(
            NlpIssue(
                field=field,
                reason="long_sentences",
                snippet=text[:160],
                matches=[f"avg_words_per_sentence={avg:.1f}"],
            )
        )

    # Very long words often signal jargon.
    long_words = sorted({w.strip(".,;:()[]\"'").lower() for w in text.split() if len(w.strip(".,;:()[]\"'")) >= 14})
    if len(long_words) >= 2:
        issues.append(
            NlpIssue(
                field=field,
                reason="long_uncommon_words",
                snippet=text[:160],
                matches=long_words[:8],
            )
        )
    return issues


def needs_plain_english_rewrite(text: str) -> bool:
    return bool(check_plain_english(text))


def any_needs_plain_english(texts: list[str]) -> bool:
    return any(needs_plain_english_rewrite(t) for t in texts if t)


_ORIGINAL_MARKERS = re.compile(
    r"(?is)^\s*(?:original(?:\s+text)?|before)\s*[:\-–]\s*"
)
_REWRITTEN_MARKERS = re.compile(
    r"(?is)(?:^|\n)\s*(?:plain\s*english|rewritten|rewrite|fixed|after|corrected)"
    r"(?:\s+version)?\s*[:\-–]\s*"
)


def extract_rewritten_only(text: str) -> str:
    """Keep only the NLP rewrite when the model returns original + rewritten."""
    if not text:
        return text
    cleaned = text.strip().strip('"').strip("'")
    # If the model labeled sections, prefer the rewritten section.
    parts = _REWRITTEN_MARKERS.split(cleaned)
    if len(parts) >= 2:
        cleaned = parts[-1].strip()
    cleaned = _ORIGINAL_MARKERS.sub("", cleaned).strip()
    # Drop a leading "Original: ..." paragraph if a second paragraph remains.
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", cleaned) if p.strip()]
    if len(paragraphs) >= 2 and _ORIGINAL_MARKERS.match(paragraphs[0] + ":"):
        cleaned = "\n\n".join(paragraphs[1:]).strip()
    # Collapse duplicated consecutive sentences.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", cleaned) if s.strip()]
    deduped: list[str] = []
    seen: set[str] = set()
    for sentence in sentences:
        key = re.sub(r"\s+", " ", sentence.lower()).strip(" .")
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sentence)
    return " ".join(deduped).strip() or cleaned


def texts_overlap(a: str, b: str, *, threshold: float = 0.55) -> bool:
    """True when two strings largely repeat the same idea."""
    from app.services.duplicate_detector import is_duplicate

    return is_duplicate(a, b, threshold=threshold)


def dedupe_slide_copy(slide: dict) -> dict:
    """After NLP, keep one clear text line when title/body/highlight repeat."""
    from app.services.duplicate_detector import resolve_slide_duplicates

    out = dict(slide)
    out["title"] = extract_rewritten_only(str(out.get("title") or "")).strip()
    out["body"] = extract_rewritten_only(str(out.get("body") or "")).strip()
    if out.get("highlight"):
        out["highlight"] = extract_rewritten_only(str(out.get("highlight"))).strip() or None
    fixed, _actions = resolve_slide_duplicates(out)
    return fixed


def dedupe_carousel_copy(slides: list[dict], caption: str):
    """Extract NLP-only text, then run duplicate detector + resolver.

    Returns ``(slides, caption, duplicate_report)``.
    """
    from app.services.duplicate_detector import resolve_carousel_duplicates

    cleaned = []
    for s in slides:
        item = dict(s)
        item["title"] = extract_rewritten_only(str(item.get("title") or "")).strip()
        item["body"] = extract_rewritten_only(str(item.get("body") or "")).strip()
        if item.get("highlight"):
            item["highlight"] = extract_rewritten_only(str(item.get("highlight"))).strip() or None
        cleaned.append(item)
    return resolve_carousel_duplicates(cleaned, extract_rewritten_only(caption or "").strip())


def build_linkedin_caption(caption: str, hashtags: list[str], *, site: str = "www.cloudless.gr") -> str:
    """Caption + hashtags + site, without duplicating tags/URL already in caption."""
    text = extract_rewritten_only(caption or "").strip()
    lower = text.lower()
    tags = []
    for h in hashtags or []:
        tag = "#" + str(h).lstrip("#").strip()
        if tag == "#" or tag.lower() in lower:
            continue
        tags.append(tag)
    if site.lower() not in lower:
        text = (text + "\n\n" + site).strip() if text else site
    if tags:
        text = (text + "\n\n" + " ".join(tags)).strip()
    return text


def check_carousel_copy(slides: list[dict], caption: str) -> NlpCheckReport:
    """Run NLP vocabulary checks across carousel slides + caption."""
    issues: list[NlpIssue] = []
    issues.extend(check_plain_english(caption or "", "caption"))
    for i, slide in enumerate(slides):
        prefix = f"slide[{i}]"
        issues.extend(check_plain_english(slide.get("title") or "", f"{prefix}.title"))
        issues.extend(check_plain_english(slide.get("body") or "", f"{prefix}.body"))
        if slide.get("highlight"):
            issues.extend(check_plain_english(slide.get("highlight") or "", f"{prefix}.highlight"))
    return NlpCheckReport(needs_fix=bool(issues), issues=issues)


async def rewrite_plain_english(
    text: str,
    *,
    provider_name: str = "cloudflare",
    model: str | None = None,
    db=None,
    team_id=None,
    context: str = "social media post",
    force: bool = False,
    allow_fallback: bool = True,
) -> str:
    """Rewrite text into plain English using the configured LLM."""
    if not text or (not force and not needs_plain_english_rewrite(text)):
        return text

    from app.services.cf_models import CF_TEXT_FREE
    from app.services.inference import call_inference

    if model is None and (provider_name or "cloudflare") == "cloudflare":
        model = CF_TEXT_FREE

    prompt = f"""Rewrite this {context} in plain English so everyday people understand it immediately.

{PLAIN_ENGLISH_RULES}

Keep the same meaning and intent. Do not add new claims. Keep roughly the same length.
Return JSON with key "text" set to the rewritten string ONLY.
Do not include the original text. Do not label sections. Do not quote the original.

Text to rewrite:
\"\"\"{text}\"\"\""""

    schema = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }
    try:
        result = await call_inference(
            prompt,
            provider_name=provider_name,
            db=db,
            team_id=team_id,
            schema=schema,
            model_override=model,
            allow_fallback=allow_fallback,
        )
        rewritten = extract_rewritten_only((result.get("text") or "").strip())
        return rewritten or text
    except HTTPException:
        return text
    except Exception:
        return text


async def fix_carousel_copy(
    *,
    slides: list[dict],
    caption: str,
    provider_name: str = "cloudflare",
    model: str | None = None,
    db=None,
    team_id=None,
    force: bool = False,
    allow_fallback: bool = True,
) -> tuple[list[dict], str, list[str]]:
    """Rewrite flagged (or all, if force) carousel fields into plain English."""
    from app.services.cf_models import CF_TEXT_FREE

    if model is None and (provider_name or "cloudflare") == "cloudflare":
        model = CF_TEXT_FREE
    rewritten_fields: list[str] = []
    cleaned_slides: list[dict] = []

    for i, slide in enumerate(slides):
        out = dict(slide)
        for key, context in (
            ("title", "carousel slide title"),
            ("body", "carousel slide body"),
            ("highlight", "carousel highlight"),
        ):
            original = out.get(key)
            if not original:
                continue
            if force or needs_plain_english_rewrite(str(original)):
                fixed = await rewrite_plain_english(
                    str(original),
                    provider_name=provider_name,
                    model=model,
                    db=db,
                    team_id=team_id,
                    context=context,
                    force=True,
                    allow_fallback=allow_fallback,
                )
                if fixed != original:
                    rewritten_fields.append(f"slide[{i}].{key}")
                out[key] = extract_rewritten_only(fixed)
        cleaned_slides.append(out)

    cleaned_caption = caption or ""
    if caption and (force or needs_plain_english_rewrite(caption)):
        cleaned_caption = await rewrite_plain_english(
            caption,
            provider_name=provider_name,
            model=model,
            db=db,
            team_id=team_id,
            context="social media caption",
            force=True,
            allow_fallback=allow_fallback,
        )
        if cleaned_caption != caption:
            rewritten_fields.append("caption")
    cleaned_caption = extract_rewritten_only(cleaned_caption)

    return cleaned_slides, cleaned_caption, rewritten_fields


async def run_nlp_check_and_fix(
    *,
    slides: list[dict],
    caption: str,
    provider_name: str = "cloudflare",
    model: str | None = None,
    db=None,
    team_id=None,
    force_fix: bool = True,
    allow_fallback: bool = True,
) -> tuple[list[dict], str, NlpCheckReport]:
    """Pipeline stage: check vocabulary/clarity, then fix into plain English.

    By default ``force_fix=True`` so carousel copy always gets a plain-English pass.
    """
    from app.services.cf_models import CF_TEXT_FREE

    if model is None and (provider_name or "cloudflare") == "cloudflare":
        model = CF_TEXT_FREE
    report = check_carousel_copy(slides, caption)
    should_fix = force_fix or report.needs_fix
    if not should_fix:
        return slides, caption, report

    cleaned_slides, cleaned_caption, fields = await fix_carousel_copy(
        slides=slides,
        caption=caption,
        provider_name=provider_name,
        model=model,
        db=db,
        team_id=team_id,
        force=force_fix or report.needs_fix,
        allow_fallback=allow_fallback,
    )
    # Keep NLP text only — drop original leftovers and redundant duplicates.
    from app.services.duplicate_detector import detect_carousel_duplicates

    cleaned_slides, cleaned_caption, dup_report = dedupe_carousel_copy(
        cleaned_slides, cleaned_caption
    )
    # Re-check after fix for residual issues (informational).
    after = check_carousel_copy(cleaned_slides, cleaned_caption)
    residual_dups = detect_carousel_duplicates(cleaned_slides, cleaned_caption)
    report.fixed = True
    report.fields_rewritten = fields
    report.duplicates = {
        **dup_report.to_dict(),
        "residual_hits": [h.to_dict() for h in residual_dups.hits],
    }
    # Surface duplicate hits as NLP issues for visibility in nlp_report.
    for hit in dup_report.hits:
        report.issues.append(
            NlpIssue(
                field=f"{hit.left_field}~{hit.right_field}",
                reason=f"duplicate:{hit.reason}",
                snippet=hit.left_snippet,
                matches=[f"score={hit.score}", hit.right_snippet[:80]],
            )
        )
    # Keep original issues for transparency; append residual as notes via matches.
    if after.issues:
        report.issues.extend(
            [
                NlpIssue(
                    field=i.field,
                    reason=f"residual_after_fix:{i.reason}",
                    snippet=i.snippet,
                    matches=i.matches,
                )
                for i in after.issues
            ]
        )
    print(
        f"[nlp] check issues={len(report.issues)} fixed={report.fixed} "
        f"rewritten={report.fields_rewritten} dup_actions={dup_report.actions}",
        flush=True,
    )
    return cleaned_slides, cleaned_caption, report


async def ensure_plain_english_carousel(
    *,
    slides: list[dict],
    caption: str,
    provider_name: str = "cloudflare",
    model: str | None = None,
    db=None,
    team_id=None,
    allow_fallback: bool = True,
) -> tuple[list[dict], str]:
    """Backward-compatible helper used by generate-carousel / generate-content."""
    cleaned_slides, cleaned_caption, _report = await run_nlp_check_and_fix(
        slides=slides,
        caption=caption,
        provider_name=provider_name,
        model=model,
        db=db,
        team_id=team_id,
        force_fix=False,
        allow_fallback=allow_fallback,
    )
    return cleaned_slides, cleaned_caption

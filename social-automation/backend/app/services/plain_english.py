"""NLP plain-English checker and fixer for social / carousel copy."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from dataclasses import field as dc_field

from fastapi import HTTPException

logger = logging.getLogger(__name__)
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

# Sofia Kakkava / "Marketing on Autopilot" writing standards — the full copy
# spec distilled from the linkedin-post-writer skill (intent-driven,
# framework-heavy, story-flow templates). Injected into post-generation
# prompts alongside PLAIN_ENGLISH_RULES; the deterministic checker below
# scores drafts against the same rules.
SOFIA_POST_SPEC = """
SOFIA POST SPEC (must follow):
- Hook: first line under 12 words; start with I, You, If, When, Here's, Stop,
  Want, a number, or a quoted statement. Never open with "today", "here are X",
  "did you know", or a generic statement. Create curiosity, FOMO, or immediate
  value — never a headline, always a conversation opener.
- Sentences: mix ultra-short (1-5 words) punches with medium (8-15 words).
  Paragraphs: 1-3 sentences max, frequent line breaks.
- Voice: contractions always ("I'd", "you'll" — never "I would", "you will").
  Grade 3-4 vocabulary. Zero em dashes — use commas or periods instead.
- Specifics: at least one real number, name, or date in the first half.
  Never invent a story, metric, or credential — use generic authority
  ("I've seen this pattern") only when no real detail exists.
- No hedging ("it might be worth", "let's talk about"), no vague quantifiers
  ("many people", "lots of"), no manufactured emotion ("my heart was racing"),
  no AI phrases ("it's no secret that", "at the end of the day", "moreover",
  "furthermore", "delve", "in today's fast-paced world").
- Structure: pick ONE intent — educating (framework/numbered list),
  nurturing (story: setup→turning point→lesson), soft selling (achievement→
  two choices), hard selling (offer→benefits→CTA), or engagement (contrarian
  opinion→question). Commit fully to it.
- Close: end with a direct question or one clear CTA. Long posts (>900 chars)
  get exactly one P.S. — one idea, 8-15 words.
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

# Tokens that are not vocabulary: hashtags, URLs, @-mentions, urn:li: markup.
# They must not count toward the long-word jargon check.
_NON_VOCABULARY_TOKENS = re.compile(r"(?:https?://\S+|www\.\S+|urn:li:\S+|#\w+|@\w+)")


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

    # Very long words often signal jargon. Hashtags, URLs, mentions, and
    # urn:li: markup are not vocabulary — strip them before scanning.
    prose = _NON_VOCABULARY_TOKENS.sub(" ", text)
    long_words = sorted({w.strip(".,;:()[]\"'").lower() for w in prose.split() if len(w.strip(".,;:()[]\"'")) >= 14})
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
    r"(?i)^[ \t]*(?:original(?:[ \t]+text)?|before)[ \t]*[:\-][ \t]*"
)
_REWRITTEN_MARKERS = re.compile(
    r"(?i)(?:^|\n)[ \t]*(?:plain[ \t]+english|rewritten?|fixed|after|corrected)"
    r"(?:[ \t]+version)?[ \t]*[:\-][ \t]*"
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
    provider_name: str = "dmr",
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

    if model is None and (provider_name or "dmr") == "cloudflare":
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
    provider_name: str = "dmr",
    model: str | None = None,
    db=None,
    team_id=None,
    force: bool = False,
    allow_fallback: bool = True,
) -> tuple[list[dict], str, list[str]]:
    """Rewrite flagged (or all, if force) carousel fields into plain English."""
    from app.services.cf_models import CF_TEXT_FREE

    if model is None and (provider_name or "dmr") == "cloudflare":
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
    provider_name: str = "dmr",
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

    if model is None and (provider_name or "dmr") == "cloudflare":
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
    logger.info(
        f"[nlp] check issues={len(report.issues)} fixed={report.fixed} "
        f"rewritten={report.fields_rewritten} dup_actions={dup_report.actions}"
    )
    return cleaned_slides, cleaned_caption, report


async def ensure_plain_english_carousel(
    *,
    slides: list[dict],
    caption: str,
    provider_name: str = "dmr",
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


# ── Sofia Kakkava / "Marketing on Autopilot" copy rules ───────────────────────
# Deterministic checks distilled from the linkedin-post-writer skill
# (intent-driven.md): hook verification, forbidden AI phrases, zero em dashes,
# P.S. discipline, sentence-rhythm variation, specificity signals.
# These score *how the copy sells*, on top of the vocabulary checks above.

# Forbidden AI phrases — Sofia's blacklist merged with common AI tells.
_SOFIA_FORBIDDEN = re.compile(
    r"\b("
    r"it'?s no secret that|at the end of the day|moreover|furthermore|"
    r"in today'?s (?:fast[- ]paced|digital|ever[- ]changing) \w+|"
    r"delve|tapestry|landscape of|game[- ]?changer|elevate your|"
    r"let'?s dive in|imagine a world|"
    r"the truth is|here are \d+|in a world where|"
    r"take your \w+ to the next level|dive into|embark on|"
    # Hedging openers + vague quantifiers + manufactured emotion +
    # generic lesson clichés (intent-driven / story-flow specs).
    r"it might be worth|let'?s talk about|many people|lots of|"
    r"some people|my heart was racing|life lessons|in the end|at its core"
    r")\b",
    re.IGNORECASE,
)

_HOOK_STARTERS = re.compile(
    r"^\s*(?:[“\"']|\d|i\b|you\b|if\b|when\b|here'?s\b|stop\b|want\b|"
    r"we\b|my\b|the\b|no\b|nobody\b|everyone\b)",
    re.IGNORECASE,
)
_GENERIC_HOOK_OPENERS = re.compile(r"^\s*(?:today\b|here are\b|did you know\b|have you ever\b)", re.IGNORECASE)
_PS_LINE = re.compile(r"^p\.?s\.?[\s:—-]", re.IGNORECASE | re.MULTILINE)
_CTA_SIGNAL = re.compile(
    r"(?:\?\s*$|\b(?:comment|share|repost|save this|follow|dm me|link in bio|grab|join|sign up|check out|visit|click)\b)",
    re.IGNORECASE,
)
_HAS_NUMBER = re.compile(r"\d")
_HAS_NAME = re.compile(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,}\b")


def _sentence_word_counts(text: str) -> list[int]:
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]
    return [len(s.split()) for s in sentences]


def check_sofia_rules(text: str, field: str = "text") -> list[NlpIssue]:
    """Score copy against the Kakkava / Marketing-on-Autopilot playbook.

    Returns issues with ``reason`` in:
      ``weak_hook``, ``generic_hook``, ``ai_tell_phrases``, ``em_dash``,
      ``flat_rhythm``, ``no_specifics``, ``missing_cta``, ``missing_ps``.
    Each check maps to an explicit rule from the intent-driven template.
    """
    issues: list[NlpIssue] = []
    if not text or not text.strip():
        return issues

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    hook = lines[0] if lines else ""
    counts = _sentence_word_counts(text)

    # ── Hook: under 12 words, starts I/You/If/When/quote/stop, not generic ──
    hook_words = len(hook.split())
    if hook_words > 12:
        issues.append(
            NlpIssue(
                field=field,
                reason="weak_hook",
                snippet=hook[:160],
                matches=[f"hook_words={hook_words} (max 12)"],
            )
        )
    elif hook and not _HOOK_STARTERS.match(hook):
        issues.append(
            NlpIssue(
                field=field,
                reason="weak_hook",
                snippet=hook[:160],
                matches=["hook should start with I, You, If, When, a quote, or a command"],
            )
        )
    if _GENERIC_HOOK_OPENERS.match(hook):
        issues.append(
            NlpIssue(
                field=field,
                reason="generic_hook",
                snippet=hook[:160],
                matches=["forbidden opener: 'today', 'here are X', 'did you know', 'have you ever'"],
            )
        )

    # ── Forbidden AI phrases ──
    tells = sorted({m.group(0).lower() for m in _SOFIA_FORBIDDEN.finditer(text)})
    if tells:
        issues.append(
            NlpIssue(
                field=field,
                reason="ai_tell_phrases",
                snippet=text[:160],
                matches=tells,
            )
        )

    # ── Zero em dashes (Sofia's voice rule) ──
    if "—" in text or "--" in text:
        issues.append(
            NlpIssue(
                field=field,
                reason="em_dash",
                snippet=text[:160],
                matches=["em dash / double hyphen — rewrite as comma or period"],
            )
        )

    # ── Sentence rhythm: flag only zero variation — all sentences in one band.
    # Bands mirror the playbook: short ≤5, medium 6-19, long ≥20 words.
    if len(counts) >= 4:
        bands = {"short" if c <= 5 else "medium" if c <= 19 else "long" for c in counts}
        if len(bands) == 1:
            issues.append(
                NlpIssue(
                    field=field,
                    reason="flat_rhythm",
                    snippet=text[:160],
                    matches=["mix ultra-short (1-5 word) punches with medium sentences"],
                )
            )

    # ── Specificity: at least one number or named person ──
    if not _HAS_NUMBER.search(text) and not _HAS_NAME.search(text):
        issues.append(
            NlpIssue(
                field=field,
                reason="no_specifics",
                snippet=text[:160],
                matches=["add a real number, name, or date — vague copy doesn't convert"],
            )
        )

    # ── Close: question or CTA signal anywhere in the last ~300 chars
    # (a question followed by a P.S. still counts as closing engagement).
    tail = lines[-1] if lines else ""
    close_zone = text[-300:]
    has_cta = (
        _CTA_SIGNAL.search(tail)
        or "?" in close_zone
        or _CTA_SIGNAL.search(close_zone)
    )
    if tail and not has_cta:
        issues.append(
            NlpIssue(
                field=field,
                reason="missing_cta",
                snippet=tail[:160],
                matches=["end with a question or one clear CTA"],
            )
        )

    # ── P.S. on long-form (LinkedIn template requires it) ──
    if len(text) >= 900 and not _PS_LINE.search(text):
        issues.append(
            NlpIssue(
                field=field,
                reason="missing_ps",
                snippet=text[:160],
                matches=["long-form posts need a P.S. — one clear idea, 8-15 words"],
            )
        )

    return issues


def sofia_nlp_score(text: str) -> tuple[int, list[NlpIssue]]:
    """0-100 score for Sofia-rule compliance. Weighted: hook & AI tells hit
    hardest (they kill the scroll-stop), structure items are lighter."""
    weights = {
        "weak_hook": 15,
        "generic_hook": 15,
        "ai_tell_phrases": 15,
        "em_dash": 8,
        "flat_rhythm": 8,
        "no_specifics": 12,
        "missing_cta": 10,
        "missing_ps": 7,
        # vocabulary checks reuse the plain-English deduction
        "jargon_or_buzzwords": 20,
        "long_sentences": 20,
        "long_uncommon_words": 12,
    }
    issues = check_plain_english(text) + check_sofia_rules(text)
    score = 100
    for i in issues:
        score -= weights.get(i.reason, 10)
    return max(0, score), issues

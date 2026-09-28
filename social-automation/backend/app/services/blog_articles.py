"""Generate and publish blog articles to the cloudless.gr R2 datalake.

The site reads article JSON from ``newsletter/articles/{slug}.json`` in the
datalake bucket (see ``src/lib/blog-r2.ts`` in cloudless.gr). Each article has:

- ``slug``, ``title``, ``excerpt``, ``date``, ``readTime``, ``category``
- ``content`` — markdown-ish text rendered by the blog page. Format: intro
  paragraphs, ``## `` section headings, ``- `` bullet lists, ``**bold**``,
  `` `code` ``, ``[text](url)`` inline links (see blog/[slug]/page.tsx).
- ``html`` — newsletter-email body (the site itself does not render it).

Generation goes through DMR (platform="blog" → the 8B long-form model) and
publishing uses the same ``r2_storage.upload_object`` path as the 6h datalake
exporter. Slugs are date-prefixed so same-day re-runs are idempotent.
"""

import html
import json
import logging
import re
from datetime import UTC, datetime

from fastapi import HTTPException

from app.core.config import get_settings
from app.services import r2_storage

logger = logging.getLogger(__name__)

settings = get_settings()

ARTICLES_PREFIX = "newsletter/articles/"
VALID_CATEGORIES = ("Cloud", "Serverless", "Analytics", "AI Marketing")

# The markdown-ish schema the blog page renders — keep in sync with
# cloudless.gr src/app/[locale]/blog/[slug]/page.tsx.
ARTICLE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "excerpt": {"type": "string"},
        "category": {"type": "string"},
        "readTime": {"type": "string"},
        "contentMarkdown": {"type": "string"},
        "socialPost": {"type": "string"},
    },
    "required": ["title", "excerpt", "category", "readTime", "contentMarkdown", "socialPost"],
}


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return re.sub(r"-{2,}", "-", slug)


def default_slug(topic: str, date: datetime | None = None) -> str:
    """Date-prefixed slug — same-day retries are naturally idempotent."""
    day = (date or datetime.now(UTC)).strftime("%Y-%m-%d")
    return f"{day}-{slugify(topic)}"


def _inline_html(text: str) -> str:
    """Escape + inline-markdown to HTML: **bold**, `code`, [text](url)."""
    esc = html.escape(text, quote=False)
    esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
    esc = re.sub(r"`(.+?)`", r"<code>\1</code>", esc)
    esc = re.sub(
        r"\[(.+?)\]\((https?://[^\s)]+)\)",
        r'<a href="\2" rel="noopener noreferrer">\1</a>',
        esc,
    )
    return esc


def article_markdown_to_html(content: str) -> str:
    """Convert the blog's markdown-ish content format to the newsletter HTML
    format — mirrors the rendering rules of the blog detail page so both
    outputs stay in parity."""
    out: list[str] = []
    in_list = False
    for block in re.split(r"\n{2,}", content.strip()):
        block = block.strip()
        if not block:
            continue
        lines = [ln for ln in block.split("\n") if ln.strip()]
        if all(ln.strip().startswith("- ") for ln in lines):
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.extend(f"<li>{_inline_html(ln.strip()[2:])}</li>" for ln in lines)
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if block.startswith("## "):
            out.append(f"<h2>{_inline_html(block[3:].strip())}</h2>")
        else:
            out.append(f"<p>{_inline_html(block)}</p>")
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def normalize_article_markdown(content: str) -> str:
    """Clean model output into the exact shape the page expects: no title
    ``# `` line, only ``## `` section headings, blank-line-separated blocks."""
    text = content.strip()
    # Drop a leading "# Title" line — the page renders title from metadata.
    text = re.sub(r"\A# [^\n]*\n{1,2}", "", text)
    # The page only splits on "## " — downgrade deeper headings.
    text = re.sub(r"^#{3,6} ", "## ", text, flags=re.M)
    # Markdown headings without a space (##Title) still render as headings.
    text = re.sub(r"^##([^ \n])", r"## \1", text, flags=re.M)
    # Headings need blank lines on both sides: the page merges a "## " line's
    # block into the h2, and a heading glued to the previous line renders
    # inside that paragraph.
    text = re.sub(r"([^\n])\n(## )", r"\1\n\n\2", text)
    text = re.sub(r"^(## [^\n]+)\n(?!\n)", r"\1\n\n", text, flags=re.M)
    # Isolate "- " list runs into their own block — the page only renders a
    # <ul> when every line in the block is a bullet.
    lines = text.split("\n")
    fixed: list[str] = []
    for i, ln in enumerate(lines):
        is_bullet = ln.strip().startswith("- ")
        prev_bullet = i > 0 and lines[i - 1].strip().startswith("- ")
        if ln.strip() and is_bullet != prev_bullet and fixed and fixed[-1].strip():
            fixed.append("")
        fixed.append(ln)
    text = "\n".join(fixed)
    # Collapse excessive blank lines.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def build_article_prompt(topic: str, extra_context: str = "") -> str:
    week = datetime.now(UTC).strftime("%B %d, %Y (ISO week %V)")
    extra = f"\n\nAdditional angle/context to work in: {extra_context}" if extra_context.strip() else ""
    return f"""Write this week's edition of a blog article for cloudless.gr.

Topic: {topic}
Edition date: {week}{extra}

cloudless.gr is a Cloudflare-first, self-hosted, zero-cost-infrastructure
project. The audience is founders, builders and SMB operators who want
production-grade systems without AWS-scale bills.

Article requirements:
- 4-6 sections covering genuinely useful current cloud-computing trends:
  edge computing, serverless, AI at the edge, free-tier architecture,
  self-hosting/open-source alternatives, cost optimization, observability.
- Practical implications for founders/builders — what to try or avoid, not
  generic hype. Prefer concrete, verifiable points over bold predictions.
- Where relevant, note the Cloudflare/self-hosted/open-source angle.

Output format rules (STRICT — the renderer is not full markdown):
- contentMarkdown: intro paragraph(s), then "## " section headings, body
  paragraphs and "- " bullet lists. Blocks separated by blank lines.
- ONLY "## " headings — never "#", "###" or deeper.
- Inline formatting allowed: **bold**, `code`, [text](https://url).
- 500-800 words. No title line, no meta commentary, no conclusion fluff like
  "In conclusion".
- excerpt: one sentence, under 160 chars, plain text.
- readTime: like "6 min read" (estimate ~200 wpm).
- category: exactly one of {", ".join(VALID_CATEGORIES)}.
- socialPost: a LinkedIn company-page post (under 700 chars) promoting this
  article: hook line, 2-3 teaser bullets or one-line takeaways, a read-more
  line. Leave the URL out — the caller appends the canonical link. 3-5
  relevant hashtags at the end including #cloudcomputing."""


def article_key(slug: str) -> str:
    return f"{ARTICLES_PREFIX}{slug}.json"


def _datalake_bucket() -> str:
    bucket = (settings.DATALAKE_R2_BUCKET or "").strip()
    if not bucket:
        raise HTTPException(status_code=503, detail="DATALAKE_R2_BUCKET is not configured")
    return bucket


async def get_published_article(slug: str) -> dict | None:
    """Read back an already-published article — None if not present."""
    try:
        data = await r2_storage.get_object(article_key(slug), bucket=_datalake_bucket())
    except HTTPException as exc:
        if exc.status_code == 404:
            return None
        raise
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        logger.warning("blog_articles: corrupt article JSON at %s", article_key(slug))
        return None


async def publish_article(article: dict) -> dict:
    """Write the article JSON to the datalake bucket (idempotent by slug)."""
    payload = json.dumps(article, ensure_ascii=False).encode()
    return await r2_storage.upload_object(
        article_key(article["slug"]),
        payload,
        content_type="application/json",
        bucket=_datalake_bucket(),
    )


def clean_social_post(text: str) -> str:
    """Strip invented links from generated social copy — the canonical URL
    travels via the post's link_url field, not inline text."""
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"https?://\S+", "", text)
    # Drop "Read more"-style lead-ins left dangling by link removal —
    # standalone lines, or inline before a line break/hashtag/end.
    text = re.sub(r"(?im)^[ \t]*(read|learn|find out) more\b[:.!… ]*", "", text)
    text = re.sub(r"(?i)\b(read|learn|find out) more\b[ \t]*[:.!…]?(?=[ \t]*(?:\n|#|$))", "", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def assemble_article(slug: str, generated: dict, topic: str = "") -> dict:
    """Validate the DMR output and build the exact R2 article shape."""
    title = (generated.get("title") or "").strip()
    if not title:
        # Schema-mode occasionally drops the title field — derive a
        # deterministic edition title rather than failing the run.
        title = f"{topic or 'Weekly Cloud Computing Trends'} — {datetime.now(UTC):%B %d, %Y}"
    excerpt = (generated.get("excerpt") or "").strip()
    content_md = normalize_article_markdown(generated.get("contentMarkdown") or "")
    if not excerpt or not content_md:
        raise HTTPException(status_code=502, detail="AI did not return a usable article")

    category = generated.get("category") or "Cloud"
    if category not in VALID_CATEGORIES:
        category = "Cloud"

    return {
        "slug": slug,
        "title": title,
        "excerpt": excerpt,
        "date": datetime.now(UTC).isoformat(),
        "readTime": (generated.get("readTime") or "5 min read").strip(),
        "category": category,
        "content": content_md,
        "html": article_markdown_to_html(content_md),
        # Not part of the site's schema — stored so idempotent re-runs can
        # reuse the generated social copy. Ignored by blog-r2.ts.
        "socialPost": clean_social_post(generated.get("socialPost") or ""),
    }

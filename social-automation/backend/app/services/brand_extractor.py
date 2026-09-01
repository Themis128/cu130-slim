"""AI Brand Kit Extractor — extract brand identity from a website URL.

Fetches the website HTML, parses colors/fonts/logo/copy, then uses
Cloudflare Workers AI to analyze tone and generate a structured brand kit draft.
"""

import re
import logging
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.services.inference import call_inference

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
MAX_CONTENT_CHARS = 8000


async def extract_brand_from_url(url: str) -> dict:
    """Scrape a website and extract a draft brand kit.

    Returns a dict with brand identity, voice, and visual fields that can
    be used to pre-fill the brand creation form.
    """
    # Normalize URL
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    # Fetch homepage and about page in parallel
    async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, follow_redirects=True) as client:
        homepage_resp = await client.get(url, headers={"User-Agent": "CloudlessBrandBot/1.0"})
        homepage_html = homepage_resp.text

        about_html = ""
        about_urls = [urljoin(base_url, "/about"), urljoin(base_url, "/about-us"), urljoin(base_url, "/en/about")]
        for about_url in about_urls:
            try:
                resp = await client.get(about_url, headers={"User-Agent": "CloudlessBrandBot/1.0"})
                if resp.status_code == 200 and len(resp.text) > 500:
                    about_html = resp.text
                    break
            except Exception:
                continue

    # Parse homepage
    soup = BeautifulSoup(homepage_html, "html.parser")

    # Extract basic info
    name = _extract_brand_name(soup, parsed.netloc)
    tagline = _extract_tagline(soup)
    meta_description = _extract_meta_description(soup)

    # Extract colors
    colors = _extract_colors(soup, homepage_html)

    # Extract fonts
    fonts = _extract_fonts(soup, homepage_html)

    # Extract logo
    logo_url = _extract_logo(soup, base_url)

    # Extract text content for AI analysis
    text_content = _extract_text_content(soup)
    if about_html:
        about_soup = BeautifulSoup(about_html, "html.parser")
        about_text = _extract_text_content(about_soup)
        text_content = text_content + "\n\n" + about_text[:3000]

    text_content = text_content[:MAX_CONTENT_CHARS]

    # Use AI to analyze tone and generate positioning/mission
    ai_analysis = await _analyze_website_content(text_content, name, meta_description or tagline)

    # Build the result
    result: dict = {
        "name": name,
        "tagline": tagline,
        "website_url": url,
        "positioning_statement": ai_analysis.get("positioning_statement"),
        "mission": ai_analysis.get("mission"),
        "industry": ai_analysis.get("industry"),
        "values": ai_analysis.get("values", []),
        "competitor_names": ai_analysis.get("competitor_names", []),
        "target_audience": ai_analysis.get("target_audience", {}),
    }

    # Add visual data
    if colors:
        result["visual"] = {
            "primary_color": colors[0],
            "accent_color": colors[1] if len(colors) > 1 else colors[0],
            "neutral_colors": colors[2:] if len(colors) > 2 else [],
            "font_heading": fonts[0] if fonts else None,
            "font_body": fonts[1] if len(fonts) > 1 else (fonts[0] if fonts else None),
            "logo_url": logo_url,
            "image_style": ai_analysis.get("image_style"),
            "photography_direction": ai_analysis.get("photography_direction"),
        }

    # Add voice data
    result["voice"] = {
        "tone_dimensions": ai_analysis.get("tone_dimensions", {}),
        "messaging_pillars": ai_analysis.get("messaging_pillars", []),
        "banned_phrases": ai_analysis.get("banned_phrases", []),
        "preferred_phrases": ai_analysis.get("preferred_phrases", []),
        "example_content": text_content[:500] if text_content else None,
        "voice_signature": ai_analysis.get("voice_signature", {}),
    }

    return result


def _extract_brand_name(soup: BeautifulSoup, domain: str) -> str:
    """Extract brand name from title, h1, or meta tags."""
    # Try <title>
    title = soup.find("title")
    if title:
        text = title.get_text(strip=True)
        # Remove common suffixes
        for sep in [" — ", " | ", " - ", "  "]:
            if sep in text:
                text = text.split(sep)[0].strip()
                break
        if text:
            return text

    # Try <h1>
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(strip=True)
        if text and len(text) < 100:
            return text

    # Fall back to domain
    return domain.replace("www.", "").split(".")[0].capitalize()


def _extract_tagline(soup: BeautifulSoup) -> str | None:
    """Extract tagline from meta description or first prominent text."""
    desc = _extract_meta_description(soup)
    if desc and len(desc) < 200:
        return desc

    # Try first <p> after an <h1>
    h1 = soup.find("h1")
    if h1:
        sibling = h1.find_next_sibling("p")
        if sibling:
            text = sibling.get_text(strip=True)
            if text and len(text) < 200:
                return text

    return None


def _extract_meta_description(soup: BeautifulSoup) -> str | None:
    """Extract meta description."""
    meta = soup.find("meta", attrs={"name": "description"})
    if meta and meta.get("content"):
        return meta["content"].strip()
    meta = soup.find("meta", attrs={"property": "og:description"})
    if meta and meta.get("content"):
        return meta["content"].strip()
    return None


def _extract_colors(soup: BeautifulSoup, html: str) -> list[str]:
    """Extract brand colors from CSS and inline styles."""
    colors: set[str] = set()

    # Hex colors from inline styles and <style> tags
    hex_pattern = re.compile(r"#([0-9a-fA-F]{6})\b")
    for match in hex_pattern.finditer(html):
        colors.add("#" + match.group(1).lower())

    # rgb() colors
    rgb_pattern = re.compile(r"rgb\((\d+),\s*(\d+),\s*(\d+)\)")
    for match in rgb_pattern.finditer(html):
        r, g, b = int(match.group(1)), int(match.group(2)), int(match.group(3))
        colors.add(f"#{r:02x}{g:02x}{b:02x}")

    # CSS custom properties (--primary, --accent, etc.)
    css_var_pattern = re.compile(r"--(?:primary|accent|brand|color)['"]?\s*:\s*(#[0-9a-fA-F]{6}|rgb\([^)]+\))")
    for match in css_var_pattern.finditer(html):
        val = match.group(1)
        if val.startswith("#"):
            colors.add(val.lower())
        else:
            rgb_match = rgb_pattern.match(val)
            if rgb_match:
                r, g, b = int(rgb_match.group(1)), int(rgb_match.group(2)), int(rgb_match.group(3))
                colors.add(f"#{r:02x}{g:02x}{b:02x}")

    # theme-color meta tag
    theme_meta = soup.find("meta", attrs={"name": "theme-color"})
    if theme_meta and theme_meta.get("content"):
        colors.add(theme_meta["content"].lower())

    # Sort by frequency in the HTML (most used = primary)
    color_freq: dict[str, int] = {}
    for c in colors:
        color_freq[c] = html.lower().count(c)

    sorted_colors = sorted(colors, key=lambda c: color_freq.get(c, 0), reverse=True)
    return sorted_colors[:6]


def _extract_fonts(soup: BeautifulSoup, html: str) -> list[str]:
    """Extract font families from CSS."""
    fonts: list[str] = []
    font_pattern = re.compile(r"font-family\s*:\s*([^;}{]+)", re.IGNORECASE)
    seen: set[str] = set()

    for match in font_pattern.finditer(html):
        raw = match.group(1).strip()
        # Take the first font name (before any comma)
        first = raw.split(",")[0].strip().strip("'\"")
        if first and first.lower() not in seen and first.lower() not in ("inherit", "initial", "system-ui", "sans-serif", "serif", "monospace"):
            seen.add(first.lower())
            fonts.append(first)

    return fonts[:4]


def _extract_logo(soup: BeautifulSoup, base_url: str) -> str | None:
    """Extract logo URL from header/nav images or favicon."""
    # Try header/nav img
    for selector in ["header img", "nav img", ".logo img", ".navbar img", '[class*="logo"] img']:
        img = soup.select_one(selector)
        if img and img.get("src"):
            return urljoin(base_url, img["src"])

    # Try favicon as fallback
    favicon = soup.find("link", attrs={"rel": "icon"}) or soup.find("link", attrs={"rel": "shortcut icon"})
    if favicon and favicon.get("href"):
        return urljoin(base_url, favicon["href"])

    return None


def _extract_text_content(soup: BeautifulSoup) -> str:
    """Extract readable text content from the page."""
    # Remove script and style tags
    for tag in soup(["script", "style", "noscript", "svg", "nav", "footer"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)
    # Collapse multiple newlines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


async def _analyze_website_content(text: str, brand_name: str, tagline: str | None) -> dict:
    """Use Cloudflare Workers AI to analyze website content and extract brand signals."""
    if not text.strip():
        return {}

    prompt = f"""Analyze the following website content for the brand "{brand_name}".
Tagline: {tagline or "N/A"}

Website content:
---
{text[:4000]}
---

Return a JSON object with these fields:
{{
  "industry": "the industry/category this brand operates in",
  "positioning_statement": "A positioning statement in the format: For [audience] who [need], [brand] is [category] that [benefit]. Unlike [alternatives], [brand] [differentiator].",
  "mission": "The brand's mission statement (1-2 sentences)",
  "values": ["3-5 core values this brand stands for"],
  "competitor_names": ["3-5 competitor names if mentioned or implied"],
  "target_audience": {{
    "demographics": "Who they are (company size, roles, location)",
    "pain_points": "What problems they have",
    "goals": "What they want to achieve"
  }},
  "tone_dimensions": {{
    "formality": 1-5,
    "playfulness": 1-5,
    "authority": 1-5,
    "friendliness": 1-5,
    "technical": 1-5
  }},
  "messaging_pillars": [
    {{"pillar": "name", "description": "one sentence description"}}
  ],
  "banned_phrases": ["3-5 phrases this brand would NEVER use based on its tone"],
  "preferred_phrases": ["3-5 phrases this brand uses or would prefer"],
  "voice_signature": {{
    "tone": "one word",
    "style": "short description",
    "persona": "who the brand sounds like"
  }},
  "image_style": "description of the visual style for AI image generation",
  "photography_direction": "direction for photography"
}}

Return ONLY the JSON object, no other text."""

    try:
        result = await call_inference(
            prompt=prompt,
            provider_name="cloudflare",
            schema={
                "type": "object",
                "properties": {
                    "industry": {"type": "string"},
                    "positioning_statement": {"type": "string"},
                    "mission": {"type": "string"},
                    "values": {"type": "array", "items": {"type": "string"}},
                    "competitor_names": {"type": "array", "items": {"type": "string"}},
                    "target_audience": {"type": "object"},
                    "tone_dimensions": {"type": "object"},
                    "messaging_pillars": {"type": "array", "items": {"type": "object"}},
                    "banned_phrases": {"type": "array", "items": {"type": "string"}},
                    "preferred_phrases": {"type": "array", "items": {"type": "string"}},
                    "voice_signature": {"type": "object"},
                    "image_style": {"type": "string"},
                    "photography_direction": {"type": "string"},
                },
            },
        )
        return result.get("response", result)
    except Exception as e:
        logger.warning("AI brand analysis failed for %s: %s", brand_name, e)
        return {}

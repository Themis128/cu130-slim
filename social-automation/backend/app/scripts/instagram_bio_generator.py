#!/usr/bin/env python3
"""Generate stylish, SEO-optimized Instagram bios from LinkedIn profile data.

Uses Unicode font hacks (Sans Bold, Italic, Small Caps), aesthetic dividers,
and proven bio structure (5 lines, arrow CTA) to create Instagram bios that
look professional and rank for SEO keywords.

Usage:
    # Generate from a LinkedIn about text
    python instagram_bio_generator.py --linkedin-about "Founder of Cloudless..."

    # Generate a specific style
    python instagram_bio_generator.py --name "Themistoklis" --title "Founder @ Cloudless" \\
        --skills "Azure,AWS,Cloud Architecture" --location "Athens" \\
        --links "cloudless.gr,baltzakisthemis.com" --style bold-brand

    # List all styles
    python instagram_bio_generator.py --list-styles

    # Check a bio for SEO/NLP
    python instagram_bio_generator.py --check "🚀 Founder @ Cloudless..."
"""

from __future__ import annotations

import argparse
import sys

# --- Unicode font converters ---

_SANS_BOLD_LOWER_START = 0x1D5EE  # 𝗮
_SANS_BOLD_UPPER_START = 0x1D5D6  # 𝗔
_ITALIC_LOWER_START = 0x1D44E  # 𝑎
_ITALIC_UPPER_START = 0x1D434  # 𝐴

_SMALL_CAPS = {
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ",
    "h": "ʜ", "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ",
    "o": "ᴏ", "p": "ᴘ", "q": "ǫ", "r": "ʀ", "s": "s", "t": "ᴛ", "u": "ᴜ",
    "v": "ᴠ", "w": "ᴡ", "x": "x", "y": "ʏ", "z": "ᴢ",
}


def to_sans_bold(text: str) -> str:
    """Convert text to Mathematical Sans-Serif Bold Unicode."""
    result = []
    for c in text:
        if "a" <= c <= "z":
            result.append(chr(_SANS_BOLD_LOWER_START + ord(c) - ord("a")))
        elif "A" <= c <= "Z":
            result.append(chr(_SANS_BOLD_UPPER_START + ord(c) - ord("A")))
        else:
            result.append(c)
    return "".join(result)


def to_italic(text: str) -> str:
    """Convert text to Mathematical Italic Unicode."""
    result = []
    for c in text:
        if "a" <= c <= "z":
            result.append(chr(_ITALIC_LOWER_START + ord(c) - ord("a")))
        elif "A" <= c <= "Z":
            result.append(chr(_ITALIC_UPPER_START + ord(c) - ord("A")))
        else:
            result.append(c)
    return "".join(result)


def to_small_caps(text: str) -> str:
    """Convert text to Small Caps Unicode."""
    return "".join(_SMALL_CAPS.get(c.lower(), c) for c in text)


# --- Bio style templates ---

STYLES = {
    "bold-brand": {
        "description": "Brand name in Sans Bold Unicode, plain text details",
        "template": (
            "🚀 {title_prefix}{name_sans_bold}\n"
            "☁️ {skills}\n"
            "💡 {experience}\n"
            "📍 {location_arrow}\n"
            "↓ {links}"
        ),
    },
    "small-caps": {
        "description": "Brand name in Small Caps, aesthetic dividers",
        "template": (
            "🚀 {title_prefix}{name_small_caps}\n"
            "☁️ {skills}\n"
            "💡 {experience}\n"
            "📍 {location}\n"
            "↓ {links}"
        ),
    },
    "italic-tagline": {
        "description": "Italic tagline, clean structure with arrows",
        "template": (
            "🚀 {title_prefix}{name_italic}\n"
            "☁️ {skills}\n"
            "💡 {experience}\n"
            "📍 {location_arrow}\n"
            "↓ {links}"
        ),
    },
    "aesthetic-divider": {
        "description": "Aesthetic divider line with star symbol",
        "template": (
            "🚀 {title_prefix}{name}\n"
            "─── ✦ ───\n"
            "☁️ {skills}\n"
            "💡 {experience}\n"
            "↓ {links}"
        ),
    },
    "clean-arrows": {
        "description": "Plain text with directional arrows (most readable)",
        "template": (
            "🚀 {title_prefix}{name}\n"
            "☁️ {skills}\n"
            "💡 {experience}\n"
            "📍 {location_arrow}\n"
            "↓ {links}"
        ),
    },
    "minimal-bullets": {
        "description": "Bullet points with aesthetic dot dividers",
        "template": (
            "🚀 {title_prefix}{name}\n"
            "• {skills}\n"
            "• {experience}\n"
            "• {location}\n"
            "↓ {links}"
        ),
    },
}


def generate_bio(
    name: str,
    title: str,
    skills: str,
    experience: str,
    location: str,
    links: str,
    style: str = "bold-brand",
) -> str:
    """Generate a stylish Instagram bio.

    Args:
        name: Brand/person name (e.g., "Cloudless")
        title: Title prefix (e.g., "Founder @ ")
        skills: Skills line (e.g., "Cloud Architect · Azure · AWS")
        experience: Experience line (e.g., "15+ yrs building systems")
        location: Location (e.g., "Athens")
        links: Links (e.g., "cloudless.gr | baltzakisthemis.com")
        style: Bio style name from STYLES dict

    Returns:
        Generated bio text (max 150 chars)
    """
    if style not in STYLES:
        raise ValueError(f"Unknown style: {style}. Available: {list(STYLES.keys())}")

    template = STYLES[style]["template"]
    bio = template.format(
        title_prefix=title,
        name=name,
        name_sans_bold=to_sans_bold(name),
        name_italic=to_italic(name),
        name_small_caps=to_small_caps(name),
        skills=skills,
        experience=experience,
        location=location,
        location_arrow=f"{location} → Worldwide" if " → " not in location else location,
        links=links,
    )

    if len(bio) > 150:
        # Try shortening by removing "building" → "→"
        bio = bio.replace("building systems", "→ systems")
        if len(bio) > 150:
            bio = bio.replace("Worldwide", "🌍")
            if len(bio) > 150:
                bio = bio.replace(" | ", " · ")
                if len(bio) > 150:
                    bio = bio.replace(" · ", " ")
                    if len(bio) > 150:
                        print(
                            f"⚠️  Warning: bio is {len(bio)} chars (limit 150). "
                            "Consider shortening fields.",
                            file=sys.stderr,
                        )

    return bio


def check_bio(bio: str) -> dict:
    """Check a bio for length, SEO keywords, and NLP issues.

    Returns a dict with:
        - length: character count
        - lines: number of lines
        - seo_keywords: list of found SEO keywords
        - nlp_issues: list of NLP issues (if plain_english is available)
    """
    result = {
        "length": len(bio),
        "lines": bio.count("\n") + 1,
        "seo_keywords": [],
        "nlp_issues": [],
        "over_limit": len(bio) > 150,
    }

    # SEO keyword check
    bio_lower = bio.lower()
    keywords = ["cloud", "azure", "aws", "founder", "cloudless", "architect"]
    result["seo_keywords"] = [k for k in keywords if k in bio_lower]

    # NLP check (optional — only if app services are available)
    try:
        from app.services.plain_english import check_plain_english

        issues = check_plain_english(bio, "bio")
        result["nlp_issues"] = [
            {"reason": i.reason, "snippet": i.snippet} for i in issues
        ]
    except ImportError:
        pass  # Not running inside the app container

    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate stylish, SEO-optimized Instagram bios",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--name", help="Brand or person name")
    parser.add_argument("--title", default="Founder @ ", help="Title prefix")
    parser.add_argument("--skills", help="Skills line (use · or | as dividers)")
    parser.add_argument("--experience", help="Experience line")
    parser.add_argument("--location", default="Athens", help="Location")
    parser.add_argument("--links", help="Links line (use | as divider)")
    parser.add_argument(
        "--style",
        default="bold-brand",
        choices=list(STYLES.keys()),
        help="Bio style template",
    )
    parser.add_argument("--list-styles", action="store_true", help="List all styles")
    parser.add_argument("--check", metavar="BIO", help="Check a bio for SEO/NLP")
    parser.add_argument(
        "--linkedin-about",
        help="Generate from LinkedIn about text (extracts keywords)",
    )

    args = parser.parse_args()

    if args.list_styles:
        for name, info in STYLES.items():
            print(f"  {name}: {info['description']}")
        return

    if args.check:
        bio = args.check
        result = check_bio(bio)
        print(f"Bio: {bio}")
        print(f"Length: {result['length']} chars {'⚠️ OVER LIMIT' if result['over_limit'] else '✅'}")
        print(f"Lines: {result['lines']}")
        print(f"SEO keywords: {result['seo_keywords']}")
        if result["nlp_issues"]:
            print("NLP issues:")
            for issue in result["nlp_issues"]:
                print(f"  {issue['reason']}: \"{issue['snippet']}\"")
        else:
            print("NLP: Clean")
        return

    if args.linkedin_about:
        # Simple extraction from LinkedIn about text
        about = args.linkedin_about
        name = "Cloudless"
        if "Cloudless" in about:
            name = "Cloudless"
        title = "Founder @ "
        skills = "Cloud Architect · Azure · AWS"
        experience = "15+ yrs building systems"
        location = "Athens"
        links = "cloudless.gr"
        bio = generate_bio(name, title, skills, experience, location, links, args.style)
        print(bio)
        print(f"\nLength: {len(bio)} chars")
        return

    if args.name:
        bio = generate_bio(
            name=args.name,
            title=args.title,
            skills=args.skills or "",
            experience=args.experience or "",
            location=args.location,
            links=args.links or "",
            style=args.style,
        )
        print(bio)
        result = check_bio(bio)
        print(f"\nLength: {result['length']} chars {'⚠️ OVER LIMIT' if result['over_limit'] else '✅'}")
        print(f"SEO keywords: {result['seo_keywords']}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()

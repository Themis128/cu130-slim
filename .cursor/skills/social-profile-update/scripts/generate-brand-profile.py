#!/usr/bin/env python3
"""Generate brand-aligned profile text for all platforms based on the
Cloudless brand identity. Outputs ready-to-paste bio, about, and
description text for manual entry on read-only platforms (Instagram,
Threads, Twitter/X, TikTok) and for API-driven platforms (LinkedIn, FB)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_lib"))
from skill_http import request, social_api  # noqa: E402

api, token = social_api()

print("=== Cloudless Brand Profile Text ===\n")
brand = request("GET", f"{api}/api/v1/brand", token=token)

name = brand.get("name", "Cloudless")
website = brand.get("website_url", "https://cloudless.gr")
industry = brand.get("industry", "")
tagline = brand.get("tagline", "")
mission = brand.get("mission", "")
positioning = brand.get("positioning_statement", "")
values = brand.get("values", [])
voice = brand.get("voice", {})
pillars = voice.get("messaging_pillars", [])
preferred = voice.get("preferred_phrases", [])
banned = voice.get("banned_phrases", [])
visual = brand.get("visual", {})
primary_color = visual.get("primary_color", "#0b1220")
accent_color = visual.get("accent_color", "#22d3e6")

print(f"Brand: {name}")
print(f"Website: {website}")
print(f"Industry: {industry}")
print(f"Tagline: {tagline}\n")

ig_bio = ("Clear skies. Zero friction. ☁️\n"
          "Cloud architecture, serverless & AI marketing.\n"
          f"Results in 14 days → {website}")
print("--- Instagram Bio (150 char max) ---")
print(ig_bio)
print(f"({len(ig_bio)} chars)\n")

print("--- Threads Bio (syncs with Instagram) ---")
print(ig_bio + "\n")

x_bio = ("Clear skies. Zero friction. Cloud architecture, serverless dev & "
         f"AI-powered marketing for startups and SMBs. → {website}")
print("--- Twitter/X Bio (160 char max) ---")
print(x_bio)
print(f"({len(x_bio)} chars)\n")

tiktok_bio = "Cloudless — serverless & AI marketing ☁️"
print("--- TikTok Bio (80 char max) ---")
print(tiktok_bio)
print(f"({len(tiktok_bio)} chars)\n")

linkedin_desc = f"{positioning}\n\nOur mission: {mission}\n\nWhat we do:\n"
for p in pillars:
    linkedin_desc += f'• {p["pillar"]}: {p["description"]}\n'
linkedin_desc += f'\nOur values: {", ".join(values)}\n\nVisit {website} to learn more.'
print("--- LinkedIn Company Page Description ---")
print(linkedin_desc)
print(f"({len(linkedin_desc)} chars)\n")

fb_about = ("Cloud architecture, serverless development, data analytics & "
            "AI-powered digital marketing. Clear skies, zero friction.")
print("--- Facebook Page About (short) ---")
print(fb_about)
print(f"({len(fb_about)} chars)\n")

fb_desc = (f"{positioning}\n\n{mission}\n\nWe help startups and SMBs ship faster "
           "with serverless cloud architecture, Cloudflare-first delivery, and "
           "AI-powered digital marketing. No lock-in. Transparent pricing. "
           "Results in 14 days.")
print("--- Facebook Page Description (longer) ---")
print(fb_desc)
print(f"({len(fb_desc)} chars)\n")

print("--- Visual Identity ---")
print(f"Primary color: {primary_color}")
print(f"Accent color: {accent_color}")
print(f'Image style: {visual.get("image_style", "")}\n')

print("--- Preferred Phrases ---")
print(", ".join(preferred) + "\n")
print("--- Banned Phrases (avoid) ---")
print(", ".join(banned))

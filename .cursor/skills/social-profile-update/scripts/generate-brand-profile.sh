#!/usr/bin/env bash
# Generate brand-aligned profile text for all platforms based on the Cloudless brand identity.
# Outputs ready-to-paste bio, about, and description text for manual entry on read-only platforms
# (Instagram, Threads, Twitter/X, TikTok) and for API-driven platforms (LinkedIn, Facebook).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../../../.." && pwd)"
cd "$ROOT"
source .env 2>/dev/null

TOKEN=$(curl -s -X POST http://127.0.0.1:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin).get('access_token',''))")

echo "=== Cloudless Brand Profile Text ==="
echo ""

curl -s http://127.0.0.1:8083/api/v1/brand -H "Authorization: Bearer $TOKEN" | python3 -c "
import sys, json

brand = json.load(sys.stdin)
name = brand.get('name', 'Cloudless')
tagline = brand.get('tagline', '')
website = brand.get('website_url', 'https://cloudless.gr')
industry = brand.get('industry', '')
mission = brand.get('mission', '')
positioning = brand.get('positioning_statement', '')
values = brand.get('values', [])
voice = brand.get('voice', {})
pillars = voice.get('messaging_pillars', [])
preferred = voice.get('preferred_phrases', [])
banned = voice.get('banned_phrases', [])
visual = brand.get('visual', {})
primary_color = visual.get('primary_color', '#0b1220')
accent_color = visual.get('accent_color', '#22d3e6')

print(f'Brand: {name}')
print(f'Website: {website}')
print(f'Industry: {industry}')
print(f'Tagline: {tagline}')
print()

# Instagram bio (max 150 chars)
ig_bio = f'Clear skies. Zero friction. ☁️\nCloud architecture, serverless & AI marketing.\nResults in 14 days → {website}'
print('--- Instagram Bio (150 char max) ---')
print(ig_bio)
print(f'({len(ig_bio)} chars)')
print()

# Threads bio (syncs with Instagram)
print('--- Threads Bio (syncs with Instagram) ---')
print(ig_bio)
print()

# Twitter/X bio (160 char max)
x_bio = f'Clear skies. Zero friction. Cloud architecture, serverless dev & AI-powered marketing for startups and SMBs. → {website}'
print('--- Twitter/X Bio (160 char max) ---')
print(x_bio)
print(f'({len(x_bio)} chars)')
print()

# TikTok bio (80 char max)
tiktok_bio = f'Cloudless — serverless & AI marketing ☁️'
print('--- TikTok Bio (80 char max) ---')
print(tiktok_bio)
print(f'({len(tiktok_bio)} chars)')
print()

# LinkedIn Company Page description (no hard limit, ~2000 chars)
linkedin_desc = f'{positioning}\n\nOur mission: {mission}\n\nWhat we do:\n'
for p in pillars:
    linkedin_desc += f'• {p[\"pillar\"]}: {p[\"description\"]}\n'
linkedin_desc += f'\nOur values: {\", \".join(values)}\n\nVisit {website} to learn more.'
print('--- LinkedIn Company Page Description ---')
print(linkedin_desc)
print(f'({len(linkedin_desc)} chars)')
print()

# Facebook Page about (short, ~155 chars)
fb_about = f'Cloud architecture, serverless development, data analytics & AI-powered digital marketing. Clear skies, zero friction.'
print('--- Facebook Page About (short) ---')
print(fb_about)
print(f'({len(fb_about)} chars)')
print()

# Facebook Page description (longer)
fb_desc = f'{positioning}\n\n{mission}\n\nWe help startups and SMBs ship faster with serverless cloud architecture, Cloudflare-first delivery, and AI-powered digital marketing. No lock-in. Transparent pricing. Results in 14 days.'
print('--- Facebook Page Description (longer) ---')
print(fb_desc)
print(f'({len(fb_desc)} chars)')
print()

# Visual identity reference
print('--- Visual Identity ---')
print(f'Primary color: {primary_color}')
print(f'Accent color: {accent_color}')
print(f'Image style: {visual.get(\"image_style\", \"\")}')
print()

# Preferred/banned phrases
print('--- Preferred Phrases ---')
print(', '.join(preferred))
print()
print('--- Banned Phrases (avoid) ---')
print(', '.join(banned))
"

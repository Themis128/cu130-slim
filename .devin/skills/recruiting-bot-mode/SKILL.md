---
name: recruiting-bot-mode
description: >-
  Recruiting bot mode for personal Facebook Messenger auto-reply. Detects
  recruiting-related keywords (job, position, role, hiring, recruiter, CV,
  resume, interview) and responds with a professional summary, CV link, and
  availability status. Covers the system prompt configuration, keyword
  detection, and response template on the personal Messenger auto-reply
  endpoint. Use when configuring the personal Messenger bot for recruiting
  inquiries, updating the CV link, or debugging recruiting keyword
  detection.
allowed-tools:
  - read
  - exec
  - grep
  - web_search
triggers:
  - user
  - model
---

# Recruiting Bot Mode (Personal Messenger Auto-Reply)

The personal Messenger auto-reply includes a dedicated **RECRUITING BOT MODE**
that detects recruiting-related keywords and responds with a professional
summary, CV link, and availability status. This handles recruiter outreach
automatically without requiring Themis to respond personally to every
initial inquiry.

## When to use

- Configure the personal Messenger bot to handle recruiting inquiries
- Update the CV link or LinkedIn URL in the recruiting response
- Debug recruiting keyword detection
- Update the professional summary in the auto-reply
- Change the recruiting response template
- Disable/enable recruiting bot mode

## How it works

When a message contains recruiting-related keywords, the auto-reply
responds with a pre-defined professional summary instead of the generic
assistant response:

```
Hi! This is Themis automated assistant. Themis is a Software & Data Engineer
with 15+ years in enterprise IT, MSc in Data Analytics, AWS certified, and
currently open to opportunities. You can find his full CV at
baltzakisthemis.com or LinkedIn: linkedin.com/in/baltzakis-themis. For
specific questions, he will reply personally soon.
```

## Recruiting keywords

The system prompt detects these recruiting-related keywords:

```
job, position, role, opportunity, hiring, recruiter, talent, vacancy,
career, interview, CV, resume, portfolio
```

## Configuration

### API endpoint

```
PUT /api/v1/messenger/{account_id}/personal/auto-reply
Authorization: Bearer <token>
Content-Type: application/json
```

### Account ID

The personal Messenger account ID:
```
de7234c0-0209-4243-a08f-ce7b96f1ebc7
```

### Full configuration

```bash
TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  -d "username=${SOCIAL_ADMIN_EMAIL}&password=${SOCIAL_ADMIN_PASSWORD}" \
  | python3 -c "import sys,json; print(json.loads(sys.stdin.read())['access_token'])")

curl -X PUT "http://localhost:8083/api/v1/messenger/de7234c0-0209-4243-a08f-ce7b96f1ebc7/personal/auto-reply" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "system_prompt": "...(see below)...",
    "model": "@cf/meta/llama-3.1-8b-instruct",
    "fallback_text": "Thanks for your message! This is an automated assistant on behalf of Themis. He will get back to you personally soon. For business inquiries visit https://cloudless.gr, for CV/portfolio visit baltzakisthemis.com",
    "max_tokens": 250,
    "cooldown_seconds": 300,
    "temperature": 0.6
  }'
```

### System prompt structure

The system prompt includes these sections:

1. **IDENTITY & DISCLOSURE** — Automated assistant, not Themis himself
2. **ABOUT THEMIS** — Full CV-derived professional background
3. **ABOUT CLOUDLESS.GR** — Business info for business inquiries
4. **RECRUITING BOT MODE** — Priority handling for recruiting keywords
5. **LANGUAGE** — Match incoming message language (Greek/English)
6. **RESPONSE STYLE** — Meta best practices (brief, natural, predictable)
7. **INTENT HANDLING** — Business, recruiting, personal, greetings, spam
8. **SAFETY** — Never invent facts, never claim to be human

### Recruiting bot mode section

```
RECRUITING BOT MODE (priority handling):
When a message contains recruiting-related keywords (job, position, role,
opportunity, hiring, recruiter, talent, vacancy, career, interview, CV,
resume, portfolio), respond with:
"Hi! This is Themis automated assistant. Themis is a Software & Data Engineer
with 15+ years in enterprise IT, MSc in Data Analytics, AWS certified, and
currently open to opportunities. You can find his full CV at
baltzakisthemis.com or LinkedIn: linkedin.com/in/baltzakis-themis. For
specific questions, he will reply personally soon."
```

## CV information included

The system prompt contains these CV-derived fields:

| Field | Value |
|-------|-------|
| Name | Themistoklis Baltzakis |
| Title | Software & Data Engineer \| Founder of Cloudless.gr |
| Location | Koropi, Attica, Greece |
| Education | MSc Data Analytics (UoGM 2024-2025), BSc CS (HOU 2014-2022) |
| Certifications | AWS CCP, Cisco DevNet Associate, CDTP IoT, Cisco CCNAv7 |
| Experience | 15+ years (Boehringer Ingelheim, Skaramangas, Cisco/Estara, TITAN, JTI, Nielsen, AIA, Germanos/OTE) |
| Current role | Infra & Software Tools Engineer at Boehringer Ingelheim + Founder of Cloudless.gr |
| Languages | Greek (native), English (C1/C2) |
| Website | baltzakisthemis.com |
| LinkedIn | linkedin.com/in/baltzakis-themis |
| GitHub | github.com/Themis128 |

## AI model

```
@cf/meta/llama-3.1-8b-instruct
```

Cloudflare Workers AI (free-first). Falls back to DMR (local) then static
text if CF is unavailable.

## Cooldown

```
cooldown_seconds: 300  (5 minutes per conversation)
```

Prevents the bot from replying to every message in a rapid conversation.
After sending a reply, waits 5 minutes before replying to the same thread.

## Verification

```bash
# Check current auto-reply config
curl -s "http://localhost:8083/api/v1/messenger/de7234c0-0209-4243-a08f-ce7b96f1ebc7/personal/auto-reply" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Verify recruiting bot mode is in the prompt
curl -s "http://localhost:8083/api/v1/messenger/de7234c0-0209-4243-a08f-ce7b96f1ebc7/personal/auto-reply" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('RECRUITING BOT MODE' in d.get('system_prompt',''))"
```

## Source files

- `social-automation/backend/app/api/messenger.py` — Auto-reply endpoint
- `social-automation/backend/app/worker/tasks/personal_messenger.py` — Polling task
- `social-automation/backend/app/services/messenger_chatbot.py` — AI response generation

## Future enhancements

- Dynamic recruiting response based on current availability (open/closed)
- CV file attachment in recruiting response (PDF)
- Recruiting inquiry logging (track who reached out)
- Priority notification for recruiting messages (email/push)
- Multi-language recruiting responses (auto-detect Greek vs English)
- Integration with ATS (Applicant Tracking System) for auto-forwarding

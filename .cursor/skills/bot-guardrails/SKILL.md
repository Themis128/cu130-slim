---
name: bot-guardrails
description: >-
  Guardrails for automated social behavior: rate limits, human-like pacing, anti-spam patterns, plus the recruiting bot mode (personal Messenger auto-reply with CV link). Use when configuring automation behavior or the recruiting auto-reply.
---

# Bot Guardrails

Consolidated skill — each section below was a standalone skill. Member scripts/templates live under `<source-skill>/` inside this directory.

| Section | Source |
|---|---|
| Bot Guardrails | `bot-guardrails` |
| Recruiting Bot Mode (Personal Messenger Auto-Reply) | `bot-guardrails` |

## Bot Guardrails

Deterministic guardrail layer for SocialAuto bots. Intercepts high-stakes
intents (pricing, recruiting, human escalation) BEFORE the LLM and returns
hardcoded, brand-safe responses. Validates LLM output AFTER generation to
block banned phrases, enforce bot disclosure, and prevent price hallucination.

Based on the **Deterministic Guardrails** pattern (distilledpatterns.org),
**NeMo Guardrails** (NVIDIA), and the **Deterministic LLM Sandwich** pattern
(agentpatternscatalog/patterns). The guardrail logic is separate from the
model — versioned, tested, observable, and stable across model retraining
or prompt changes.

### When to use

- Add or edit deterministic intent handlers (pricing, recruiting, escalation)
- Validate bot replies for banned phrases after LLM generation
- Test the guardrail layer in isolation
- Debug why a bot reply was intercepted or rejected
- Review the guardrail configuration (keywords, responses, thresholds)

### Architecture

```
User Message
    │
    ▼
┌─────────────────────────────────────┐
│  1. Pre-LLM Deterministic Layer     │
│  (keyword detection, zero-token)    │
│                                     │
│  ┌─ Pricing? ─────────────────────┐ │
│  │  Keywords: how much, price,    │ │
│  │  cost, πόσο, τιμή, κόστος...   │ │
│  │  → Hardcoded response (no LLM) │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌─ Recruiting? ───────────────────┐ │
│  │  Keywords: job, position, role, │ │
│  │  hiring, recruiter, CV...       │ │
│  │  → Hardcoded response (no LLM) │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌─ Human escalation? ─────────────┐ │
│  │  Keywords: urgent, emergency,  │ │
│  │  human, agent, help...          │ │
│  │  → Hardcoded response (no LLM) │ │
│  └────────────────────────────────┘ │
│                                     │
│  If no deterministic match:         │
│     → Continue to LLM generation   │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  2. LLM Generation                   │
│  (Cloudflare Workers AI → DMR)      │
└──────────────┬──────────────────────┘
               │
               ▼
┌─────────────────────────────────────┐
│  3. Post-LLM Validation Layer       │
│                                     │
│  ┌─ Banned phrases? ───────────────┐ │
│  │  Check reply against brand      │ │
│  │  banned phrases list            │ │
│  │  → If found: retry or fallback  │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌─ Bot disclosure? ────────────────┐ │
│  │  Check first-contact disclosure │ │
│  │  → If missing: prepend prefix   │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌─ Price hallucination? ──────────┐ │
│  │  Check for €/$ amounts in reply│ │
│  │  → If found: replace with safe │ │
│  │    response                     │ │
│  └────────────────────────────────┘ │
│                                     │
│  ┌─ Language match? ───────────────┐ │
│  │  Check reply language matches  │ │
│  │  user message language          │ │
│  │  → If mismatch: retry or flag   │ │
│  └────────────────────────────────┘ │
└──────────────┬──────────────────────┘
               │
               ▼
         Final Reply
```

### Implementation

The guardrail layer is implemented in:

```
social-automation/backend/app/services/messenger_chatbot.py
```

#### Pre-LLM deterministic handlers

- `_is_pricing_question(message)` — keyword detection for pricing questions
- `_is_greek_message(message)` — Greek character detection for language routing
- `_PRICING_RESPONSES` — hardcoded Greek + English pricing responses
- `_PRICING_KEYWORDS` — English + Greek pricing keywords

#### Post-LLM validation (planned)

- Banned phrase check against brand voice API
- Bot disclosure prefix enforcement
- Price amount detection and replacement
- Language mismatch detection

### Tool scripts

Run from repo root `cu130-slim/`:

```bash
## Test the pre-LLM pricing guardrail
.devin/skills/bot-guardrails/scripts/test-pricing-guardrail.py

## Test the pre-LLM recruiting guardrail
.devin/skills/bot-guardrails/scripts/test-recruiting-guardrail.py

## Test post-LLM banned phrase validation
.devin/skills/bot-guardrails/scripts/test-banned-phrases.py

## Test language matching (Greek/English)
.devin/skills/bot-guardrails/scripts/test-language-match.py

## Run all guardrail tests
.devin/skills/bot-guardrails/scripts/test-all.py

## View current guardrail configuration
.devin/skills/bot-guardrails/scripts/show-config.py

## Test full bot reply pipeline with guardrails
.devin/skills/bot-guardrails/scripts/test-full-pipeline.py
```

### Configuration

Guardrail configuration is stored in `messenger_chatbot.py`:

| Config | Location | Purpose |
|--------|----------|---------|
| `_PRICING_KEYWORDS` | `messenger_chatbot.py` | Keywords that trigger pricing safeguard |
| `_PRICING_RESPONSES` | `messenger_chatbot.py` | Hardcoded Greek + English pricing responses |
| Brand banned phrases | Brand system API | Fetched via `_get_brand_voice_block()` |
| Brand preferred phrases | Brand system API | Fetched via `_get_brand_voice_block()` |

### Adding new deterministic handlers

To add a new deterministic handler (e.g., for hours/location questions):

1. Add keyword list to `messenger_chatbot.py`
2. Add hardcoded responses (Greek + English)
3. Add detection function (`_is_xxx_question`)
4. Add intercept in `generate_contextual_reply` before the LLM call
5. Test with `test-all.py`

### Important notes

- The guardrail logic must be separate from the model (versioned, tested, observable)
- Pre-checks must be cheap (keyword matching, not model inference)
- Post-checks must catch what the model gets wrong, not what is merely surprising
- One repair attempt, then fallback — don't loop indefinitely
- The LLM is the last resort, not the default for known intents

## Recruiting Bot Mode (Personal Messenger Auto-Reply)

The personal Messenger auto-reply includes a dedicated **RECRUITING BOT MODE**
that detects recruiting-related keywords and responds with a professional
summary, CV link, and availability status. This handles recruiter outreach
automatically without requiring Themis to respond personally to every
initial inquiry.

### When to use

- Configure the personal Messenger bot to handle recruiting inquiries
- Update the CV link or LinkedIn URL in the recruiting response
- Debug recruiting keyword detection
- Update the professional summary in the auto-reply
- Change the recruiting response template
- Disable/enable recruiting bot mode

### How it works

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

### Recruiting keywords

The system prompt detects these recruiting-related keywords:

```
job, position, role, opportunity, hiring, recruiter, talent, vacancy,
career, interview, CV, resume, portfolio
```

### Configuration

#### API endpoint

```
PUT /api/v1/messenger/{account_id}/personal/auto-reply
Authorization: Bearer <token>
Content-Type: application/json
```

#### Account ID

The personal Messenger account ID:
```
de7234c0-0209-4243-a08f-ce7b96f1ebc7
```

#### Full configuration

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

#### System prompt structure

The system prompt includes these sections:

1. **IDENTITY & DISCLOSURE** — Automated assistant, not Themis himself
2. **ABOUT THEMIS** — Full CV-derived professional background
3. **ABOUT CLOUDLESS.GR** — Business info for business inquiries
4. **RECRUITING BOT MODE** — Priority handling for recruiting keywords
5. **LANGUAGE** — Match incoming message language (Greek/English)
6. **RESPONSE STYLE** — Meta best practices (brief, natural, predictable)
7. **INTENT HANDLING** — Business, recruiting, personal, greetings, spam
8. **SAFETY** — Never invent facts, never claim to be human

#### Recruiting bot mode section

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

### CV information included

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

### AI model

```
@cf/meta/llama-3.1-8b-instruct
```

Cloudflare Workers AI (free-first). Falls back to DMR (local) then static
text if CF is unavailable.

### Cooldown

```
cooldown_seconds: 300  (5 minutes per conversation)
```

Prevents the bot from replying to every message in a rapid conversation.
After sending a reply, waits 5 minutes before replying to the same thread.

### Verification

```bash
## Check current auto-reply config
curl -s "http://localhost:8083/api/v1/messenger/de7234c0-0209-4243-a08f-ce7b96f1ebc7/personal/auto-reply" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

## Verify recruiting bot mode is in the prompt
curl -s "http://localhost:8083/api/v1/messenger/de7234c0-0209-4243-a08f-ce7b96f1ebc7/personal/auto-reply" \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; d=json.load(sys.stdin); print('RECRUITING BOT MODE' in d.get('system_prompt',''))"
```

### Source files

- `social-automation/backend/app/api/messenger.py` — Auto-reply endpoint
- `social-automation/backend/app/worker/tasks/personal_messenger.py` — Polling task
- `social-automation/backend/app/services/messenger_chatbot.py` — AI response generation

### Future enhancements

- Dynamic recruiting response based on current availability (open/closed)
- CV file attachment in recruiting response (PDF)
- Recruiting inquiry logging (track who reached out)
- Priority notification for recruiting messages (email/push)
- Multi-language recruiting responses (auto-detect Greek vs English)
- Integration with ATS (Applicant Tracking System) for auto-forwarding

# Bot Guardrails

Deterministic guardrail layer for SocialAuto bots. Intercepts high-stakes
intents (pricing, recruiting, human escalation) BEFORE the LLM and returns
hardcoded, brand-safe responses. Validates LLM output AFTER generation to
block banned phrases, enforce bot disclosure, and prevent price hallucination.

Based on the **Deterministic Guardrails** pattern (distilledpatterns.org),
**NeMo Guardrails** (NVIDIA), and the **Deterministic LLM Sandwich** pattern
(agentpatternscatalog/patterns). The guardrail logic is separate from the
model — versioned, tested, observable, and stable across model retraining
or prompt changes.

## When to use

- Add or edit deterministic intent handlers (pricing, recruiting, escalation)
- Validate bot replies for banned phrases after LLM generation
- Test the guardrail layer in isolation
- Debug why a bot reply was intercepted or rejected
- Review the guardrail configuration (keywords, responses, thresholds)

## Architecture

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

## Implementation

The guardrail layer is implemented in:

```
social-automation/backend/app/services/messenger_chatbot.py
```

### Pre-LLM deterministic handlers

- `_is_pricing_question(message)` — keyword detection for pricing questions
- `_is_greek_message(message)` — Greek character detection for language routing
- `_PRICING_RESPONSES` — hardcoded Greek + English pricing responses
- `_PRICING_KEYWORDS` — English + Greek pricing keywords

### Post-LLM validation (planned)

- Banned phrase check against brand voice API
- Bot disclosure prefix enforcement
- Price amount detection and replacement
- Language mismatch detection

## Tool scripts

Run from repo root `cu130-slim/`:

```bash
# Test the pre-LLM pricing guardrail
.devin/skills/bot-guardrails/scripts/test-pricing-guardrail.sh

# Test the pre-LLM recruiting guardrail
.devin/skills/bot-guardrails/scripts/test-recruiting-guardrail.sh

# Test post-LLM banned phrase validation
.devin/skills/bot-guardrails/scripts/test-banned-phrases.sh

# Test language matching (Greek/English)
.devin/skills/bot-guardrails/scripts/test-language-match.sh

# Run all guardrail tests
.devin/skills/bot-guardrails/scripts/test-all.sh

# View current guardrail configuration
.devin/skills/bot-guardrails/scripts/show-config.sh

# Test full bot reply pipeline with guardrails
.devin/skills/bot-guardrails/scripts/test-full-pipeline.sh
```

## Configuration

Guardrail configuration is stored in `messenger_chatbot.py`:

| Config | Location | Purpose |
|--------|----------|---------|
| `_PRICING_KEYWORDS` | `messenger_chatbot.py` | Keywords that trigger pricing safeguard |
| `_PRICING_RESPONSES` | `messenger_chatbot.py` | Hardcoded Greek + English pricing responses |
| Brand banned phrases | Brand system API | Fetched via `_get_brand_voice_block()` |
| Brand preferred phrases | Brand system API | Fetched via `_get_brand_voice_block()` |

## Adding new deterministic handlers

To add a new deterministic handler (e.g., for hours/location questions):

1. Add keyword list to `messenger_chatbot.py`
2. Add hardcoded responses (Greek + English)
3. Add detection function (`_is_xxx_question`)
4. Add intercept in `generate_contextual_reply` before the LLM call
5. Test with `test-all.sh`

## Important notes

- The guardrail logic must be separate from the model (versioned, tested, observable)
- Pre-checks must be cheap (keyword matching, not model inference)
- Post-checks must catch what the model gets wrong, not what is merely surprising
- One repair attempt, then fallback — don't loop indefinitely
- The LLM is the last resort, not the default for known intents

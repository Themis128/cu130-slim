#!/usr/bin/env bash
# Test the pre-LLM pricing guardrail
# Verifies that pricing questions are intercepted before the LLM
# and return hardcoded responses (Greek + English)
set -euo pipefail
cd "$(git rev-parse --show-toplevel 2>/dev/null || echo /home/tbaltzakis/cu130-slim)"

echo "=== Pricing Guardrail Test ==="
echo ""

docker compose exec -T social-api python3 -c "
import asyncio
from app.services.messenger_chatbot import _is_pricing_question, _PRICING_RESPONSES, _is_greek_message

# Test pricing keyword detection
test_cases = [
    # English pricing questions
    ('How much does a website cost?', True, 'english'),
    ('What is the price of your services?', True, 'english'),
    ('How expensive are you?', True, 'english'),
    ('What are your rates?', True, 'english'),
    ('Do you have a pricing plan?', True, 'english'),
    ('What is the subscription fee?', True, 'english'),
    # Greek pricing questions
    ('Πόσο κάνει ένα website;', True, 'greek'),
    ('Πόσο κοστίζει;', True, 'greek'),
    ('Ποια είναι η τιμή;', True, 'greek'),
    ('Πόσο ακριβό είναι;', True, 'greek'),
    ('Πόσο είναι η συνδρομή;', True, 'greek'),
    # Non-pricing (should NOT trigger)
    ('What services do you offer?', False, 'english'),
    ('Tell me about your company', False, 'english'),
    ('Πες μου για την εταιρεία', False, 'greek'),
    ('Hello, how are you?', False, 'english'),
    ('Γεια σου, τι κάνεις;', False, 'greek'),
]

passed = 0
failed = 0
for msg, expected_pricing, expected_lang in test_cases:
    is_pricing = _is_pricing_question(msg)
    detected_lang = 'greek' if _is_greek_message(msg) else 'english'
    lang_ok = detected_lang == expected_lang
    pricing_ok = is_pricing == expected_pricing
    
    status = 'PASS' if (pricing_ok and lang_ok) else 'FAIL'
    if status == 'PASS':
        passed += 1
    else:
        failed += 1
    print(f'  [{status}] {msg[:50]:50s} pricing={is_pricing} lang={detected_lang}')

print(f'')
print(f'Results: {passed} passed, {failed} failed')

# Test hardcoded responses
print(f'')
print(f'Greek response: {_PRICING_RESPONSES[\"greek\"]}')
print(f'English response: {_PRICING_RESPONSES[\"english\"]}')

# Verify no dollar signs in responses
for lang, resp in _PRICING_RESPONSES.items():
    if '\$' in resp or 'USD' in resp:
        print(f'  [FAIL] {lang} response contains dollar/USD!')
        failed += 1
    else:
        print(f'  [PASS] {lang} response has no dollar/USD')

# Verify cloudless.gr is mentioned
for lang, resp in _PRICING_RESPONSES.items():
    if 'cloudless.gr' in resp:
        print(f'  [PASS] {lang} response mentions cloudless.gr')
    else:
        print(f'  [FAIL] {lang} response does NOT mention cloudless.gr')
        failed += 1

# Verify bot disclosure
for lang, resp in _PRICING_RESPONSES.items():
    if 'bot' in resp.lower() or 'ρομπότ' in resp.lower() or 'αυτόματ' in resp.lower():
        print(f'  [PASS] {lang} response discloses bot identity')
    else:
        print(f'  [FAIL] {lang} response does NOT disclose bot identity')
        failed += 1

exit(1 if failed > 0 else 0)
" 2>&1

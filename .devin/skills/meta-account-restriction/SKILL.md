# Meta Account Restriction & Appeal

Diagnose and resolve Meta account restrictions that block business verification
and App Review. Use when the personal Facebook account is restricted from
advertising, when business verification shows "Your account is restricted right
now", or when the Account Quality page shows a disabled ad account.

## Current restriction (as of 2026-09-11)

| Field | Value |
|-------|-------|
| Personal account | Themistoklis Baltzakis |
| Ad account ID | `657781691826702` |
| Ad account status | **Disabled** |
| Restricted since | Jan 24, 2021 |
| Restriction type | Permanent advertising restriction |
| Reason | Non-compliance with Advertising Standards (business assets) |
| Appeal available | **No** — "Too much time has passed since this account was disabled, so this decision can't be reviewed" |
| Business portfolio | `cloudless.gr` (ID: `1558125105019725`) — No advertising issues |
| Business verification | **Blocked** — requires admin in good standing |

## What's restricted

The personal ad account restriction blocks:
- Can't create or run ads
- Can't use or share audiences
- Can't use Meta Pixel, offline event sets, or custom conversions
- Can't use app SDKs to send app events
- Can't manage advertising assets or people for businesses
- **Can't complete business verification** (requires admin in good standing)

## Key URLs

| Resource | URL |
|----------|-----|
| Account Status | `https://www.facebook.com/account_status` |
| Account Quality | `https://www.facebook.com/accountquality` |
| Business Support Home | `https://www.facebook.com/business-support-home/` |
| Ad account detail | `https://www.facebook.com/business-support-home/1134463867/657781691826702/` |
| Business portfolio detail | `https://www.facebook.com/business-support-home/1558125105019725/` |
| Meta AI Business Assistant | Available from Business Support Home sidebar |

## Diagnosis flow

### 1. Check Account Status (personal)

Navigate to `https://www.facebook.com/account_status`. This shows the personal
account health. As of 2026-09-11, this page shows "Your account looks good!" —
**this is misleading**. The restriction is on the advertising side, not the
personal account integrity side.

### 2. Check Business Support Home — My Accounts

Navigate to `https://www.facebook.com/business-support-home/?landing_page=overview`.
This shows:
- **Facebook account**: Themistoklis Baltzakis — "Account restricted"
- **Business portfolios**: cloudless.gr — "No advertising issues"

Click the restricted Facebook account to see:
- The restriction date and reason
- What's disabled (ad account, audiences, pixels, etc.)
- "What you can do" section (may show no appeal option)

### 3. Check ad account detail

Navigate to `https://www.facebook.com/business-support-home/1134463867/657781691826702/`.
This shows the specific ad account restriction. If the appeal window has
expired, the text will say:

> "Too much time has passed since this account was disabled, so this decision
> can't be reviewed."

There will be **no "Request Review" button**.

### 4. Chat with Meta AI Business Assistant

From Business Support Home, click "Meta AI business assistant" in the sidebar.
The AI can:
- Diagnose the restriction type
- Confirm whether it's on the personal account or business portfolio
- **Cannot** create a formal support ticket
- **Cannot** escalate to a live agent (may say "support team is at full capacity")
- Direct you to Account Quality for appeals

## Resolution options

### Option A: Submit an appeal (if available)

If the ad account detail page shows a "Request Review" button:
1. Click "Request Review"
2. Select a reason for the review
3. Write a clear, factual explanation:
   - Acknowledge the issue
   - Explain what you've done to fix it
   - Describe steps to prevent future violations
4. Submit — processing takes 48 hours to 2 weeks
5. Track in Support Inbox

**Current status**: Appeal is NOT available (time window expired).

### Option B: Create a new ad account

The restriction is on the specific ad account (`657781691826702`), not on
creating new ad accounts. From the ad account detail page:
1. Click "Go to Business Settings"
2. Add a new ad account in the business portfolio
3. Use the new ad account for advertising going forward

**Note**: This does NOT resolve the business verification block — the
restriction is on the personal account, not just the ad account.

### Option C: Add a second admin

The fastest path to unblock business verification:
1. Have a trusted person (with a Facebook account in good standing) log in
2. Add them as an admin of the Cloudless app:
   `https://developers.facebook.com/apps/1936126137016578/roles/`
3. Add them as an admin of the cloudless.gr business portfolio
4. Have them complete business verification
5. Once verified, the App Review submission can proceed

### Option D: Report a problem to Facebook

Use Facebook's "Report a Problem" feature to escalate:
1. Go to `https://www.facebook.com/`
2. Click profile picture (top right) > Help & support > Report a problem
3. Click "Include" (to include logs and diagnostics)
4. Write a detailed description of the issue
5. Attach screenshots of the restriction and the App Review block
6. **Required**: Use "Capture Screen" to select a screen area (cannot be
   automated via Playwright — requires manual screen selection)
7. Click "Submit report"

See the `meta-support-report` skill for the report template and screenshot
requirements.

### Option E: Meta Business Help Center contact forms

Try these direct contact forms (availability varies by account):
- `https://www.facebook.com/help/contact/6359191084165019` — General object
- `https://www.facebook.com/help/contact/2725860640553550` — Ad account
  restriction appeal (may redirect if not eligible)

## Important notes

- **Account Status vs Account Quality**: Account Status
  (`facebook.com/account_status`) shows personal account health and may show
  "looks good" even when advertising features are restricted. Account Quality
  (`facebook.com/accountquality`) shows advertising-specific restrictions.
- **Permanent restrictions**: If Meta says "too much time has passed", the
  self-serve appeal path is closed. The only options are: add a second admin,
  create a new ad account, or escalate through support channels.
- **Business verification requires an admin in good standing**: The admin who
  starts verification must not have advertising restrictions on their personal
  account.
- **The Meta AI business assistant is a chatbot**: It can diagnose but cannot
  create formal support tickets or escalate to live agents.

## Scripts

- `scripts/check-account-status.sh` — Check personal account status via Graph API
- `scripts/check-ad-account.sh` — Check ad account restriction status
- `scripts/check-business-portfolio.sh` — Check business portfolio health
- `scripts/print-appeal-template.sh` — Print an appeal template for submission

## Related skills

- `meta-app-review` — App Review submission and permission management
- `meta-support-report` — Report a Problem to Facebook support
- `meta-oauth-setup` — OAuth configuration for Meta platforms

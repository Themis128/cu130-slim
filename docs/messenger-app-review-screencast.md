# Messenger Bot — App Review Screencast Script

## Video Requirements
- Duration: 2-3 minutes
- Format: MP4, MOV, or WMV
- Show: OAuth flow + Messenger bot in action
- Narrate: What the bot does and how it works

## Screencast Script

### Scene 1: Introduction (10 seconds)
**Narration:** "This is SocialAuto by Cloudless — a social media automation platform with an AI-powered Messenger bot for Facebook Pages."

**Action:** Show the SocialAuto dashboard at https://social.cloudless.gr

### Scene 2: Connected Accounts (15 seconds)
**Narration:** "We have two Facebook Pages connected — Cloudless.gr and cloudless.gr — both with Messenger enabled."

**Action:** Navigate to Accounts page, show both Pages connected with Messenger status active.

### Scene 3: Messenger Profile Configuration (20 seconds)
**Narration:** "Each Page has a Messenger Profile configured with a Get Started button, persistent menu, and ice breakers."

**Action:** Show the Messenger inbox UI, point out the menu items (Home, Contact, Pricing, Free Audit, Website) and ice breaker questions.

### Scene 4: AI Auto-Reply Configuration (20 seconds)
**Narration:** "AI auto-reply is enabled with a brand-aware system prompt. The bot uses Cloudflare Workers AI (free tier) with a local model fallback."

**Action:** Show the auto-reply configuration — enabled=true, system prompt with brand DNA, model selection.

### Scene 5: Live Bot Test — Text Message (30 seconds)
**Narration:** "Let's test the bot by sending a message to the Page."

**Action:** Open Facebook Messenger, send a message to the cloudless.gr Page: "Hi! What services does Cloudless offer?"

**Narration:** "The webhook receives the message, the sidecar processes it, and the AI generates a brand-aware reply."

**Action:** Show the bot replying within a few seconds with a response mentioning serverless infrastructure, data analytics, and AI-powered marketing.

### Scene 6: Live Bot Test — Menu Button (20 seconds)
**Narration:** "Now let's test the persistent menu buttons."

**Action:** Tap the "Pricing" menu button in Messenger.

**Narration:** "The bot receives the postback and responds with pricing information."

**Action:** Show the bot's reply about transparent pricing and free audit.

### Scene 7: Live Bot Test — Ice Breaker (20 seconds)
**Narration:** "Ice breakers help users start conversations."

**Action:** Tap an ice breaker question: "Can I get a free audit?"

**Narration:** "The bot responds with information about the free 30-minute audit."

### Scene 8: Conversation in Inbox (15 seconds)
**Narration:** "All conversations are stored and can be reviewed in the SocialAuto Messenger inbox."

**Action:** Show the Messenger inbox in SocialAuto with the conversation history.

### Scene 9: Webhook Flow (15 seconds)
**Narration:** "The webhook is verified and receives real-time events from Meta."

**Action:** Show the sidecar status — events received, processed, auto-replies sent.

### Scene 10: Privacy & Data Deletion (10 seconds)
**Narration:** "We have a privacy policy and data deletion callback endpoint as required by Meta."

**Action:** Show https://social.cloudless.gr/privacy and https://social.cloudless.gr/data-deletion

## Test Instructions for Reviewers

### Prerequisites
- The bot works for app admins, developers, and testers during development
- No special account needed — just message the Page on Messenger

### Steps to Test
1. Open Facebook Messenger (mobile or web)
2. Search for "cloudless.gr" or "Cloudless.gr" Page
3. Send any text message (e.g., "Hello, what do you do?")
4. Wait 3-5 seconds for the AI to generate a reply
5. The bot will respond with a brand-aware answer about Cloudless services
6. Try the menu buttons at the bottom:
   - "Home" — general info
   - "Contact" — contact details
   - "Pricing" — pricing info
   - "Free Audit" — free audit offer
   - "Website" — opens cloudless.gr
7. Try the ice breaker questions:
   - "What services does Cloudless offer?"
   - "How much does it cost?"
   - "Can I get a free audit?"
   - "How fast can you deliver results?"

### Expected Behavior
- Bot replies within 3-5 seconds
- Replies are in English (or the language the user writes in)
- Replies mention Cloudless services: serverless infrastructure, data analytics, AI-powered marketing
- Replies follow brand voice: bold, professional, concise
- Menu buttons trigger postback responses
- Ice breakers start guided conversations

### Permissions Used
- `pages_messaging` — send/receive messages
- `pages_show_list` — list Pages
- `pages_manage_metadata` — set Messenger Profile
- `pages_read_engagement` — read message engagement
- `pages_manage_posts` — publish posts (separate feature)

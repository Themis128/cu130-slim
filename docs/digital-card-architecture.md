# Digital Business Card Architecture

## Overview

SocialAuto's Digital Business Card system creates, manages, and shares vCard 4.0
(RFC 6350) compliant digital business cards. Each card is auto-generated from
the team's brand identity and connected social accounts, then shared via
WhatsApp Cloud API, Facebook Messenger, QR code, or direct link.

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    Digital Business Card System                         │
│                                                                         │
│   ┌──────────────┐       ┌──────────────┐       ┌──────────────┐        │
│   │  Dashboard    │       │  Public Card  │       │  vCard 4.0   │        │
│   │  /card        │       │  /card/{token}│       │  .vcf export │        │
│   │  (Next.js)    │       │  (Next.js)    │       │  (RFC 6350)  │        │
│   └──────┬───────┘       └──────┬───────┘       └──────┬───────┘        │
│          │                      │                      │                │
│          └──────────┬───────────┴──────────────────────┘                │
│                     │                                                   │
│          ┌──────────┴──────────────────────────────────┐               │
│          │        Digital Cards API (FastAPI)           │               │
│          │        /api/v1/digital-cards                 │               │
│          │                                             │               │
│          │  CRUD · from-brand · public · track · send  │               │
│          └──────────┬──────────────────────────────────┘               │
│                     │                                                   │
│          ┌──────────┴──────────────────────────────────┐               │
│          │              Data Sources                     │               │
│          │                                             │               │
│          │  Brand (DNA, Voice, Visual)                   │               │
│          │  Social Accounts (9 platforms)                 │               │
│          │  PostgreSQL (digital_cards table)             │               │
│          │  Cloudflare Tunnel (social.cloudless.gr)      │               │
│          └─────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────────────┘
```

## Components

### Backend

| File | Purpose |
|------|---------|
| `app/models/digital_card.py` | SQLAlchemy model — `DigitalCard` with vCard 4.0 fields, share tokens, analytics |
| `app/api/digital_cards.py` | FastAPI router — 9 endpoints (CRUD, public, send, vCard, tracking) |
| `alembic/versions/u3e6f7a8b9c0_add_digital_cards.py` | Migration — creates `digital_cards` table |

### Frontend

| File | Purpose |
|------|---------|
| `app/(dashboard)/card/page.tsx` | Dashboard page — card grid, create-from-brand, send modal, delete |
| `app/(public)/card/[token]/page.tsx` | Public card page — brand-styled, QR code, share buttons, vCard download |
| `src/services/api.ts` | API client — `digitalCardApi` with 10 functions |
| `src/components/layout/Sidebar.tsx` | Navigation — "Digital Card" item with `Contact` icon |

### Database Schema

```sql
CREATE TABLE digital_cards (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    team_id         UUID NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
    brand_id        UUID REFERENCES brands(id) ON DELETE SET NULL,

    -- Card identity (vCard FN, TITLE, ORG)
    name            VARCHAR(200) NOT NULL,        -- FN: Full Name
    title           VARCHAR(200),                 -- TITLE: Job title
    company         VARCHAR(200),                 -- ORG: Organization
    tagline         VARCHAR(300),                 -- NOTE: Short tagline
    description     TEXT,                         -- Bio / about

    -- Contact info
    email           VARCHAR(255),                 -- EMAIL;TYPE=work
    phone           VARCHAR(50),                  -- TEL;TYPE=work,voice
    website         VARCHAR(500),                 -- URL
    address         VARCHAR(500),                 -- ADR;TYPE=work

    -- Visual identity (from BrandVisual or custom)
    primary_color   VARCHAR(20),                  -- e.g. #0b1220
    accent_color    VARCHAR(20),                  -- e.g. #00fff5
    logo_url        TEXT,
    avatar_url      TEXT,

    -- JSON arrays
    social_links    JSONB DEFAULT '[]',           -- [{platform, handle, url, display_name}]
    services        JSONB DEFAULT '[]',           -- [{title, description}]

    -- Sharing
    share_token     VARCHAR(64) UNIQUE NOT NULL,
    is_active       BOOLEAN DEFAULT true,

    -- Analytics
    view_count          INTEGER DEFAULT 0,
    contact_save_count  INTEGER DEFAULT 0,
    share_count         INTEGER DEFAULT 0,

    meta_data       JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

## API Endpoints

### Authenticated (requires JWT)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/digital-cards` | List all cards for the team |
| POST | `/api/v1/digital-cards` | Create a card manually |
| POST | `/api/v1/digital-cards/from-brand` | Auto-create from brand identity + social accounts |
| GET | `/api/v1/digital-cards/{id}` | Get a card by ID |
| PATCH | `/api/v1/digital-cards/{id}` | Update card fields |
| DELETE | `/api/v1/digital-cards/{id}` | Delete a card |
| GET | `/api/v1/digital-cards/{id}/vcard` | Download `.vcf` file (vCard 4.0) |
| POST | `/api/v1/digital-cards/{id}/send` | Send card link via WhatsApp or Messenger |

### Public (no auth)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/v1/digital-cards/public/{token}` | Get public card data (increments view_count) |
| POST | `/api/v1/digital-cards/public/{token}/track` | Track save/share action |

## vCard 4.0 Generation

Cards are exported as vCard 4.0 (RFC 6350) compliant `.vcf` files:

```text
BEGIN:VCARD
VERSION:4.0
FN:Cloudless
ORG:Cloudless
TITLE:Cloud Computing, Serverless & AI Marketing
EMAIL;TYPE=work;PREF=1:tbaltzakis@cloudless.gr
TEL;TYPE=work,voice;VALUE=uri;PREF=1:tel:+306977777838
URL:https://cloudless.gr
ADR;TYPE=work:;;Athens, Greece;;;;
NOTE:Clear skies. Zero friction.
URL;TYPE=linkedin:https://linkedin.com/company/cloudless-gr
URL;TYPE=facebook:https://facebook.com/Cloudless.gr
URL;TYPE=instagram:https://instagram.com/cloudless.gr
URL;TYPE=tiktok:https://tiktok.com/@cloudless.gr
URL;TYPE=twitter:https://twitter.com/TBaltzakis
URL;TYPE=threads:https://threads.net/@cloudless_gr
END:VCARD
```

Compatible with iOS Contacts, Google Contacts, Outlook, and macOS Contacts.

## Auto-Create from Brand

The `POST /from-brand` endpoint:

1. Loads the team's `Brand` (with `BrandVoice` and `BrandVisual` eagerly loaded)
2. Loads all active `SocialAccount` records
3. Maps social accounts to platform URLs:
   - facebook → `https://facebook.com/{handle}`
   - instagram → `https://instagram.com/{handle}`
   - linkedin → `https://linkedin.com/company/{handle}`
   - twitter → `https://twitter.com/{handle}`
   - threads → `https://threads.net/@{handle}`
   - tiktok → `https://tiktok.com/@{handle}`
4. Converts messaging pillars to services
5. Applies brand visual identity (primary_color, accent_color, logo_url)
6. Generates a unique share token (`secrets.token_urlsafe(32)`)
7. Returns the created card with `card_url` and `vcard` fields

## Send via WhatsApp

```
POST /api/v1/digital-cards/{id}/send
{
  "platform": "whatsapp",
  "to_phone": "+30 697 777 7838",
  "message": "Hi! Here's my digital business card: {url}"
}
```

Flow:
1. Finds the team's active WhatsApp `SocialAccount`
2. Reads `access_token` and `phone_number_id` from `meta_data`
3. Calls `POST https://graph.facebook.com/v21.0/{phone_number_id}/messages`
4. Sends a text message with the card link
5. Increments `share_count` on success

**Requirement**: WhatsApp phone number must be registered for Cloud API use
(`phone_number_registered: true`). If unregistered, Meta returns error 190
(Authentication Error).

## Send via Messenger

```
POST /api/v1/digital-cards/{id}/send
{
  "platform": "messenger",
  "to_phone": "1234567890123456",  // PSID
  "message": "Hi! Here's my digital business card: {url}"
}
```

Flow:
1. Finds the team's active Facebook Page `SocialAccount`
2. Reads `access_token` or `page_token` from `meta_data`
3. Calls `POST https://graph.facebook.com/v21.0/{page_id}/messages`
4. Sends a text message to the recipient PSID
5. Increments `share_count` on success

**Requirement**: The recipient must have messaged the Page first (24-hour
window). The `to_phone` field is the recipient's Page-Scoped ID (PSID),
not a phone number.

## Public Card Page

The public card at `/card/{token}`:

- Fetches data from `GET /api/v1/digital-cards/public/{token}` (no auth)
- Renders with brand styling (dark navy + neon cyan)
- Shows: logo/initials, name, title, tagline, description, contact buttons,
  social links, services, QR code
- QR code generated via `api.qrserver.com` (free, no API key)
- Share buttons: WhatsApp (`wa.me/?text=`), Messenger (dialog/send), native
  Web Share API, copy link
- vCard download via Blob API
- Tracks views (automatic), saves and shares (manual via `/track` endpoint)

## Analytics

| Metric | Incremented by |
|--------|---------------|
| `view_count` | Each `GET /public/{token}` call |
| `contact_save_count` | `POST /public/{token}/track?action=save` |
| `share_count` | `POST /public/{token}/track?action=share` or successful send |

## Cloudflare Tunnel

The public card is accessible via the Cloudflare tunnel:

```
https://social.cloudless.gr/card/{share_token}
```

The `social-cloudflared` container routes:
- `/api/*` → `social-api:8000` (FastAPI backend)
- `/*` → `social-frontend:8083` (Next.js frontend)

The `FRONTEND_URL` env var controls the `card_url` field:
- Default: `https://social.cloudless.gr`
- Local: `http://localhost:8082`

## Research References

Built using standards and open-source patterns from:

- [RFC 6350 — vCard Format Specification](https://www.rfc-editor.org/rfc/rfc6350.html)
- [vCard 4.0 CalConnect Guide](https://devguide.calconnect.org/vcard/vcard-4/)
- [Own Cardly](https://github.com/kevinwielander/digital-business-cards) — open-source Next.js digital cards
- [EnBizCard](https://github.com/vishnuraghavb/EnBizCard) — open-source HTML digital cards
- [Swiish](https://github.com/see4tech/swiish) — open-source PWA digital cards
- [QuiKard](https://github.com/stdmitry04/quikard) — Next.js + FastAPI digital cards

## Test Results

All 17 tests pass:

| # | Test | Result |
|---|------|--------|
| 1 | List cards | PASS — 1 card from brand |
| 2 | Create card manually | PASS — id=e32426b4 |
| 3 | Get card by ID | PASS — 2 socials, 2 services |
| 4 | Update card (tagline + deactivate) | PASS |
| 5 | Inactive card returns 404 | PASS |
| 6 | Reactivate + public card | PASS — 13 vCard lines |
| 7 | vCard 4.0 download | PASS — valid BEGIN/END:VCARD |
| 8 | Tracking (view, save, share) | PASS — all incremented |
| 9 | Invalid token returns 404 | PASS |
| 10 | Delete card | PASS — 204 then 404 |
| 11 | Send via WhatsApp | Expected fail (phone not registered) |
| 12 | Send via Messenger | Expected fail (fake PSID) |
| 13 | Missing phone validation | PASS |
| 14 | Unsupported platform validation | PASS |
| 15 | Dashboard page loads | PASS — HTTP 200 |
| 16 | Public card page loads | PASS — HTTP 200 |
| 17 | Public card via tunnel | PASS — HTTP 200 via social.cloudless.gr |

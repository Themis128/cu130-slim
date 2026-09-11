# WhatsApp Business Platform Architecture

## System Overview

```mermaid
graph TB
    subgraph "Customers"
        C1[Customer Phone 1]
        C2[Customer Phone 2]
        CN[Customer Phone N]
    end

    subgraph "Meta Platform"
        WA[WhatsApp Cloud API]
        WB[WhatsApp Business Account<br/>WABA]
        WPN[Business Phone Number]
        WBH[Meta Webhook System]
        MDASH[Meta App Dashboard<br/>1936126137016578]
    end

    subgraph "SocialAuto Stack"
        SA[social-api<br/>FastAPI :8083]
        WH[WhatsApp Router<br/>/api/v1/whatsapp/*]
        WAC[WhatsAppAPIClient<br/>whatsapp_api.py]
        WP[Webhook Parser<br/>parse_webhook_event]
        AIR[AI Reply Engine<br/>DMR → CF Workers AI]
        DB[(PostgreSQL<br/>social_automation)]
        REDIS[(Redis<br/>cooldowns)]
        CHROMA[(ChromaDB<br/>brand RAG)]
    end

    subgraph "AI Inference"
        DMR[Docker Model Runner<br/>qwen3:8b-q4_K_M<br/>localhost:12434]
        CF[Cloudflare Workers AI<br/>llama-3.1-8b-instruct<br/>FREE tier]
    end

    subgraph "Browser Bridge"
        NOVNC[noVNC Browser<br/>:6080]
        BB[Browser Bridge API<br/>:9223]
    end

    C1 & C2 & CN -->|Send messages| WA
    WA --> WB
    WB --> WPN
    WPN -->|Webhook POST| WH
    WH --> WP
    WP -->|Parse events| SA
    SA -->|Lookup account| DB
    SA -->|Check cooldown| REDIS
    SA -->|Retrieve brand context| CHROMA
    SA -->|Generate reply| AIR
    AIR -->|Primary| DMR
    AIR -->|Fallback| CF
    SA -->|Send reply| WAC
    WAC -->|POST /messages| WA
    WA -->|Deliver reply| C1 & C2 & CN

    MDASH -->|Configure webhook| WBH
    WBH -->|Verify GET| WH
```

## Registration Flow (4 Steps)

```mermaid
sequenceDiagram
    participant U as Admin User
    participant API as SocialAuto API
    participant Meta as WhatsApp Cloud API
    participant Phone as Business Phone

    Note over U,Phone: Step 1: Create phone number on WABA
    U->>API: POST /register/create-number {waba_id, cc, phone, name}
    API->>Meta: POST /{waba_id}/phone_numbers
    Meta-->>API: {"id": "110200345501442"}
    API-->>U: phone_number_id

    Note over U,Phone: Step 2: Request verification code
    U->>API: POST /register/request-code {phone_number_id, SMS, el_GR}
    API->>Meta: POST /{pnid}/request_code?code_method=SMS
    Meta->>Phone: SMS: "WhatsApp code 123-830"
    Meta-->>API: {"success": true}
    API-->>U: Code sent

    Note over U,Phone: Step 3: Verify code
    U->>API: POST /register/verify-code {phone_number_id, "123-830"}
    API->>API: Strip hyphen → "123830"
    API->>Meta: POST /{pnid}/verify_code?code=123830
    Meta-->>API: {"success": true}
    API-->>U: Phone verified

    Note over U,Phone: Step 4: Register for API use
    U->>API: POST /register/number {phone_number_id, pin:"123456"}
    API->>Meta: POST /{pnid}/register {messaging_product, pin}
    Meta-->>API: {"success": true}
    API-->>U: Number registered — ready to send/receive
```

## Webhook Message Flow

```mermaid
sequenceDiagram
    participant C as Customer
    participant Meta as WhatsApp Cloud API
    participant WH as /webhook POST
    participant Parser as parse_webhook_event
    participant DB as PostgreSQL
    participant AI as AI Reply Engine
    participant DMR as Docker Model Runner
    participant CF as Cloudflare Workers AI
    participant Sender as WhatsAppAPIClient

    C->>Meta: Sends "Does it come in blue?"
    Meta->>WH: POST webhook payload
    WH->>WH: Verify X-Hub-Signature-256 (HMAC-SHA256)
    WH->>Parser: Parse JSON body
    Parser->>Parser: Extract: phone_number_id, sender_phone, message_text, sender_name
    Parser-->>WH: Normalized event
    WH->>DB: Find account by phone_number_id
    DB-->>WH: SocialAccount {meta_data.whatsapp_auto_reply}
    WH->>WH: Check auto-reply enabled?
    WH->>WH: Check 24h customer service window
    WH->>AI: Generate reply (system_prompt + incoming_text)
    AI->>DMR: POST /chat/completions (qwen3:8b)
    alt DMR available
        DMR-->>AI: Reply text
    else DMR down
        AI->>CF: POST /ai/run/llama-3.1-8b
        CF-->>AI: Reply text
    end
    AI-->>WH: Generated reply
    WH->>Sender: send_text(sender_phone, reply)
    Sender->>Meta: POST /{phone_number_id}/messages
    Meta->>C: Delivers reply
    WH->>Sender: mark_message_read(message_id)
```

## Bot Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Unconfigured: No WhatsApp account
    Unconfigured --> Registered: 4-step phone registration
    Registered --> Connected: Account added to SocialAuto
    Connected --> BotCreated: POST /bot/create
    BotCreated --> Active: Bot enabled = true
    Active --> Inactive: POST /bot/deactivate
    Inactive --> Active: POST /bot/activate
    Active --> Paused: Per-thread pause (human handoff)
    Paused --> Active: Per-thread resume
    Active --> [*]: POST /register/deregister
    Inactive --> [*]: POST /register/deregister
```

## File Architecture

```mermaid
graph LR
    subgraph "API Layer"
        INIT[__init__.py<br/>Router registration]
        WA[whatsapp.py<br/>16 endpoints]
    end

    subgraph "Service Layer"
        WAC[whatsapp_api.py<br/>Cloud API client]
        WAC2[whatsapp_api.py<br/>Webhook parser]
    end

    subgraph "Config"
        CFG[config.py<br/>WHATSAPP_VERIFY_TOKEN]
        ENV[.env<br/>WHATSAPP_VERIFY_TOKEN]
    end

    subgraph "Skill"
        SK[SKILL.md<br/>Documentation]
        S1[check-webhook.sh]
        S2[send-test-message.sh]
        S3[setup-webhook.sh]
    end

    INIT --> WA
    WA --> WAC
    WA --> WAC2
    CFG --> WA
    ENV --> CFG
    SK --> S1 & S2 & S3
```

## Endpoint Map

```mermaid
graph LR
    subgraph "Registration"
        R1[POST /register/create-number]
        R2[POST /register/request-code]
        R3[POST /register/verify-code]
        R4[POST /register/number]
        R5[POST /register/deregister]
        R1 --> R2 --> R3 --> R4
        R4 -.-> R5
    end

    subgraph "Setup & Profile"
        S1[POST /:id/setup]
        S2[GET /:id/profile]
        S3[PUT /:id/profile]
    end

    subgraph "Messaging"
        M1[POST /:id/send<br/>text/image/document]
        M2[POST /:id/send-template<br/>initiate conversation]
    end

    subgraph "Auto-Reply"
        A1[GET /:id/auto-reply]
        A2[PUT /:id/auto-reply]
    end

    subgraph "Bot Builder"
        B1[POST /:id/bot/create]
        B2[GET /:id/bot]
        B3[PUT /:id/bot]
        B4[POST /:id/bot/activate]
        B5[POST /:id/bot/deactivate]
        B6[GET /:id/bot/personalities]
    end

    subgraph "Webhook"
        W1[GET /webhook<br/>Meta verification]
        W2[POST /webhook<br/>Incoming messages]
    end

    R4 --> S1
    S1 --> M1
    S1 --> B1
    W2 --> A2
    B1 --> A2
```

## AI Reply Decision Tree

```mermaid
flowchart TD
    MSG[Incoming WhatsApp message] --> CHECK1{Auto-reply<br/>enabled?}
    CHECK1 -->|No| END1[No action]
    CHECK1 -->|Yes| CHECK2{24h window<br/>active?}
    CHECK2 -->|No| TPL[Send template<br/>message only]
    CHECK2 -->|Yes| CHECK3{Cooldown<br/>expired?}
    CHECK3 -->|No| SKIP[Skip — rate limit]
    CHECK3 -->|Yes| CHECK4{Thread<br/>paused?}
    CHECK4 -->|Yes| SKIP
    CHECK4 -->|No| READ[Mark message read]
    READ --> DMR{DMR available?<br/>localhost:12434}
    DMR -->|Yes| GEN1[Generate via<br/>qwen3:8b local]
    DMR -->|No| CF{Cloudflare<br/>token set?}
    CF -->|Yes| GEN2[Generate via<br/>llama-3.1-8b cloud]
    CF -->|No| FALL[Use fallback text]
    GEN1 --> SEND[Send reply via<br/>Cloud API]
    GEN2 --> SEND
    FALL --> SEND
    SEND --> COOLDOWN[Set Redis cooldown<br/>300s default]
```

## Data Model

```mermaid
erDiagram
    SocialAccount ||--o{ WhatsAppConfig : "platform=whatsapp"
    SocialAccount {
        uuid id PK
        string platform "whatsapp"
        string account_type "business"
        string display_name
        string account_id "phone_number_id"
        jsonb meta_data
    }
    SocialAccount ||--|| WhatsAppConfig : "meta_data fields"
    WhatsAppConfig {
        string phone_number_id
        string access_token "encrypted"
        string display_phone_number
        jsonb whatsapp_setup
        jsonb whatsapp_auto_reply
        jsonb whatsapp_bot
    }
    WhatsAppAutoReply {
        bool enabled
        string system_prompt
        string model "ai/qwen3:8b-q4_K_M"
        string fallback_text
        int max_tokens
        int cooldown_seconds
        float temperature
    }
    WhatsAppBot {
        string name
        bool enabled
        string system_prompt
        string model
        string fallback_text
        int max_tokens
        float temperature
        int cooldown_seconds
        string business_hours_start
        string business_hours_end
        bool reply_to_spam
        bool reply_to_greetings
        list quick_replies
    }
    WhatsAppConfig ||--|| WhatsAppAutoReply : "meta_data.whatsapp_auto_reply"
    WhatsAppConfig ||--|| WhatsAppBot : "meta_data.whatsapp_bot"
```

## Comparison: Messenger vs WhatsApp

```mermaid
graph LR
    subgraph "Messenger"
        M1[Recipient: PSID<br/>Page-Scoped ID]
        M2[Webhook object: page]
        M3[Send: /page_id/messages]
        M4[Profile: /page_id/messenger_profile]
        M5[Initiate: Any time]
        M6[Account: Facebook Page]
    end
    subgraph "WhatsApp"
        W1[Recipient: Phone<br/>E.164 format]
        W2[Webhook object:<br/>whatsapp_business_account]
        W3[Send: /phone_number_id/messages]
        W4[Profile: /phone_number_id/<br/>whatsapp_business_profile]
        W5[Initiate: Template only<br/>outside 24h window]
        W6[Account: WhatsApp Business<br/>phone number]
    end
    M1 -.-> W1
    M2 -.-> W2
    M3 -.-> W3
    M4 -.-> W4
    M5 -.-> W5
    M6 -.-> W6
```

## Deployment Topology

```mermaid
graph TB
    subgraph "Host Machine (WSL2)"
        subgraph "Docker Compose Stack"
            SA[social-api<br/>:8083]
            SW1[social-worker-publishing]
            SW2[social-worker-media]
            SW3[social-worker-default]
            SW4[social-worker-messenger]
            CB[celery-beat]
            PG[(social-postgres<br/>:5432)]
            RD[(redis<br/>:6379)]
            CH[(chroma<br/>:8001)]
            NV[noVNC browser<br/>:6080]
            BB[Browser Bridge<br/>:9223]
        end
        DMR[Docker Model Runner<br/>:12434<br/>host-level]
    end

    subgraph "Cloudflare"
        CFW[Cloudflare Workers AI<br/>FREE tier]
        TUN[Cloudflare Tunnel<br/>social.cloudless.gr]
    end

    subgraph "Meta"
        WA[WhatsApp Cloud API<br/>graph.facebook.com]
        MWH[Meta Webhook System]
    end

    MWH -->|webhook POST| TUN
    TUN --> SA
    SA -->|send messages| WA
    SA -->|AI fallback| CFW
    SA -->|AI primary| DMR
    SA -->|data| PG
    SA -->|cooldowns| RD
    SA -->|brand RAG| CH
    SA -->|browser automation| BB
    BB --> NV
```

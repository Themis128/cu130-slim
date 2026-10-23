---
name: content-foundation-plan
description: >
  Builds a complete content foundation for any platform, not just LinkedIn — ICP,
  positioning, a Messaging House (UVP, message pillars, proof), a Positioning Ladder
  (point of difference vs point of parity), 4-5 ownable content pillars visualised on
  a radar chart, and a full month of content topics. Delivers one premium, animated,
  tabbed, Purely Personal-branded HTML report using the same visual system as the
  LinkedIn Profile Optimizer — stat strip, radar chart, palette-style cards, donut
  scores. Trigger on "content plan", "content foundation", "build my content pillars",
  "what should I post", "find my positioning", or during the Marketing on Autopilot
  workshop intake.
---

# Content Foundation Plan — Purely Personal
# Built for the Marketing on Autopilot workshop

You are the Content Foundation Plan skill for Purely Personal.
One job: an attendee finishes this skill knowing exactly who they're talking to, what
makes them different, what 4-5 things they can talk about forever, and exactly what
to post for the next 30 days — delivered as one HTML report at the same visual
standard as the LinkedIn Profile Optimizer report. Not a lighter version of it.

The output is ALWAYS a file saved with create_file and presented with present_files.
Never render it as an inline widget or artifact.

---

## VOICE DEFAULT

If the attendee gives their own background, ICP, or voice samples, use those completely.

If they give nothing, or say "I don't know" / "use yours" — default to Daniel Paul's
voice DNA below.

**Daniel Paul — default voice, condensed:**
Direct, specific, a little impatient with fluff, always grounded in real results.
Specificity over generality. Short sentences, most under 12 words. Never opens with
"I". Writes for one person, not "you all". Earned authority only, no claims without
numbers. Zero em dashes.

---

## STEP 1 — INTAKE

Ask all 5 in one message. Never build anything before these are answered.

```
╔══════════════════════════════════════════════════════╗
║  MARKETING ON AUTOPILOT — CONTENT FOUNDATION          ║
║  by Purely Personal                                   ║
╚══════════════════════════════════════════════════════╝

5 things to build your content foundation:

1  WHO ARE YOU?
   Name + what you do, in one line.

2  WHO DO YOU SELL TO?
   The specific person. Not "business owners" — the exact
   role, stage, or situation they're in when they need you.

3  WHAT SHOULD YOUR CONTENT DO FOR YOU?
   Leads, authority, speaking, hiring, something else —
   and what does that look like in 90 days?

4  YOUR UNIQUE ANGLE
   What do you know or believe that people in your space
   don't say out loud?

5  YOUR BIGGEST CONTENT FEAR OR GAP
   What stops you from posting consistently, or what's
   gone wrong when you've tried before?
```

**Critical rule — this is platform-agnostic.**
Never write the word "LinkedIn" in the ICP, positioning, or pillar output unless the
attendee explicitly says LinkedIn is their only channel. Say "your content," "your
audience," "where you show up."

---

## STEP 2 — ICP AND POSITIONING

From the 5 answers, write:
- A one-paragraph ICP: who they are, what they're afraid of, what they actually want
- An "Emotional Landscape" pair: the specific fear driving their audience away from action, and the specific want pulling them toward it. Two short, concrete lines, not abstractions.
- A one-sentence positioning statement, specific enough to repel the wrong audience

If the positioning could apply to three other people in the same niche, rewrite it.

---

## STEP 3 — MESSAGING HOUSE

Apply the Messaging House framework (Unique Value Proposition + supporting pillars):

- **UVP (Unique Value Proposition):** one sentence. The specific outcome, for the
  specific person, that only this person delivers this way. Not a tagline — a claim
  that could be tested and proven true or false.
- **3 Message Pillars:** the three things that have to be true for someone to believe
  the UVP. Each pillar gets a one-line proof point (a number, a result, a mechanism)
  underneath it. If there's no proof yet, write the proof they need to go get.

---

## STEP 4 — POSITIONING LADDER

Apply Ries & Trout competitive positioning:

- **Point of Difference (POD):** the one thing this person does that direct
  competitors in their niche don't, or don't say out loud.
- **Point of Parity (POP):** the baseline thing they must also be seen to do
  competently, or the POD won't be trusted.
- **Positioning Strength score (1-10):** your own honest assessment of how specific
  and defensible this positioning is right now, with one line on what would move the
  score up. Score of 10 means a competitor reading it would feel genuinely locked out
  of that territory.

---

## STEP 5 — 4-5 CONTENT PILLARS

A pillar is a territory, not a topic. "Marketing tips" is not a pillar. "Why most
service founders market to everyone and close no one" is a pillar.

For each pillar, define:
- A name that is a specific claim or territory
- One line on why only this person can own it
- The emotional or practical stake for the reader
- A weight from 1-10 for how much of their total content should sit in this pillar (they should sum to roughly even, but real priorities can be uneven — a pillar tied directly to the offer often deserves more weight)

---

## STEP 6 — 30-DAY CONTENT CALENDAR

Under all pillars combined, generate 20-24 specific content topics (roughly a month,
weighted toward higher-weight pillars from Step 5). Every topic is a real angle.

Bad: "Post about pricing"
Good: "Why I stopped discounting and started raising prices the week my best client almost left"

Tag every topic with: pillar, format (story / framework / contrarian take / case
study / quick win), and intent (educate / nurture / engage / soft sell / hard sell).

---

## STEP 7 — HTML REPORT GENERATION

Build a single self-contained HTML file using the exact visual system below. This
must match the LinkedIn Profile Optimizer's polish level — sticky tab navigation,
dark theme with a live light-mode toggle, ambient cover particles, a stat strip,
a radar chart, palette-style cards for the Messaging House, and scroll-reveal
animations throughout. This is not a stripped-down report. Every tab should feel
like it belongs in the same product family as the profile audit.

### REPORT STRUCTURE (6 tabs)

```
COVER
  · Positioning statement, large, centered
  · "Content Foundation by Purely Personal"
  · Theme toggle button, top-right (same mechanic as Profile Optimizer)

STAT STRIP
  · Positioning Strength (donut ring, score/10)
  · [N] Pillars · [N] Topics · 30 Days Planned

TAB 1 — ICP & POSITIONING
  · ICP card
  · Emotional Landscape: fear card + want card, side by side
  · Positioning statement as large pull-quote

TAB 2 — MESSAGING HOUSE
  · UVP as a palette-card style header block
  · 3 message pillar cards, each with proof point

TAB 3 — POSITIONING LADDER
  · POD / POP two-column comparison card
  · Positioning Strength donut + one-line "what moves this up"

TAB 4 — CONTENT PILLARS
  · Radar chart showing the weight of each pillar (5 axes)
  · One card per pillar underneath: name, rationale, stake, weight

TAB 5 — 30-DAY CALENDAR
  · Topics grouped by pillar, each tagged with format + intent chips

TAB 6 — HOW TO USE THIS
  · 3-step guidance, CTA footer
```

### HTML TEMPLATE

Replace all [PLACEHOLDERS]. Keep every class name, the radar chart math, and the
toggle mechanism exactly as written.

```html
<!DOCTYPE html>
<html lang="en" data-theme="dark">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>[NAME] Content Foundation — Purely Personal</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700;800;900&display=swap" rel="stylesheet">
<style>
:root {
  --primary: #E8294C; --primary-dark: #C4203D; --primary-light: #F5607A; --primary-rgb: 232,41,76;
  --bg-page: #0A0A0A; --bg-card: #111111; --bg-card-2: #191919; --bg-card-3: #1F1F1F;
  --border: rgba(255,255,255,0.07); --border-bright: rgba(255,255,255,0.12);
  --text-primary: #FFFFFF; --text-secondary: rgba(255,255,255,0.58); --text-muted: rgba(255,255,255,0.28);
  --score-high: #22C55E; --score-mid: #F59E0B; --score-low: #EF4444;
  --font: 'Poppins', sans-serif;
  --r-sm: 8px; --r-md: 16px; --r-lg: 24px; --r-xl: 32px; --r-pill: 9999px;
  --shadow-card: 0 4px 24px rgba(0,0,0,0.4); --shadow-lift: 0 16px 56px rgba(0,0,0,0.6);
  --shadow-glow: 0 0 40px rgba(var(--primary-rgb), 0.25);
}
:root[data-theme="light"] {
  --bg-page: #F8F7F5; --bg-card: #FFFFFF; --bg-card-2: #F2F1EE; --bg-card-3: #EAE9E6;
  --border: #E4E3E0; --border-bright: #D8D7D3;
  --text-primary: #0A0A0A; --text-secondary: #555555; --text-muted: #8A8A8A;
  --shadow-card: 0 4px 24px rgba(0,0,0,0.08); --shadow-lift: 0 16px 56px rgba(0,0,0,0.12);
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth;font-size:16px}
body{font-family:var(--font);background:var(--bg-page);color:var(--text-primary);-webkit-font-smoothing:antialiased;overflow-x:hidden;transition:background .3s ease,color .3s ease}
::selection{background:rgba(var(--primary-rgb),0.35)}

.theme-toggle{position:fixed;top:16px;right:16px;z-index:200;width:44px;height:44px;border-radius:50%;border:1px solid var(--border-bright);background:var(--bg-card);color:var(--text-primary);display:flex;align-items:center;justify-content:center;cursor:pointer;box-shadow:var(--shadow-card);transition:background .25s ease,border-color .25s ease,transform .15s ease}
.theme-toggle:hover{transform:scale(1.06);border-color:var(--primary)}
.theme-toggle svg{width:20px;height:20px}
.theme-toggle .icon-sun{display:none}
:root[data-theme="light"] .theme-toggle .icon-moon{display:none}
:root[data-theme="light"] .theme-toggle .icon-sun{display:block}

.cover{position:relative;overflow:hidden;padding:80px 24px 48px;text-align:center;border-bottom:1px solid var(--border)}
.cover-particle{position:absolute;border-radius:50%;background:rgba(var(--primary-rgb),0.35);animation:float var(--dur,5s) ease-in-out var(--delay,0s) infinite}
@keyframes float{0%,100%{transform:translateY(0)}50%{transform:translateY(-16px)}}
.cover__eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--primary);font-weight:600;margin-bottom:16px}
.cover__title{font-size:clamp(28px,5vw,44px);font-weight:800;max-width:760px;margin:0 auto 20px;line-height:1.25;position:relative}
.cover__sub{color:var(--text-secondary);font-size:15px;position:relative}

.stat-strip{background:var(--bg-card);border-top:1px solid var(--border);border-bottom:1px solid var(--border)}
.stat-strip__inner{max-width:960px;margin:0 auto;padding:28px 24px;display:flex;justify-content:center;gap:48px;flex-wrap:wrap;align-items:center}
.stat{text-align:center}
.stat__num{font-size:32px;font-weight:800;color:var(--primary)}
.stat__label{font-size:12px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em;margin-top:4px}
.score-donut{width:64px;height:64px}
.donut-track{fill:none;stroke:rgba(var(--primary-rgb),0.12);stroke-width:6}
.donut-fill{fill:none;stroke:var(--primary);stroke-width:6;stroke-linecap:round;stroke-dasharray:175;stroke-dashoffset:175;transition:stroke-dashoffset 1.2s cubic-bezier(.34,1.56,.64,1)}

.nav-tabs{position:sticky;top:0;z-index:100;background:rgba(10,10,10,0.85);backdrop-filter:blur(20px) saturate(180%);border-bottom:1px solid var(--border)}
:root[data-theme="light"] .nav-tabs{background:rgba(248,247,245,0.85)}
.nav-tabs__inner{max-width:960px;margin:0 auto;padding:0 24px;display:flex;overflow-x:auto;scrollbar-width:none;gap:0}
.nav-tabs__inner::-webkit-scrollbar{display:none}
.tab-btn{display:flex;align-items:center;gap:7px;padding:0 18px;height:52px;font-family:var(--font);font-size:12px;font-weight:600;color:var(--text-muted);background:none;border:none;border-bottom:2px solid transparent;cursor:pointer;white-space:nowrap;transition:color .2s,border-color .2s;letter-spacing:.02em}
.tab-btn:hover{color:var(--text-secondary)}
.tab-btn.active{color:var(--primary);border-bottom-color:var(--primary)}
.tab-btn__dot{width:6px;height:6px;border-radius:50%;background:currentColor;opacity:.6}
.tab-btn.active .tab-btn__dot{opacity:1}
.tab-panel{display:none}
.tab-panel.active{display:block}

.page{max-width:960px;margin:0 auto;padding:60px 24px 100px}
.section-title{font-size:22px;font-weight:800;margin-bottom:24px}
.card{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--r-lg);padding:28px;margin-bottom:18px;box-shadow:var(--shadow-card);opacity:0;transform:translateY(24px);transition:opacity .5s ease,transform .5s cubic-bezier(.34,1.56,.64,1)}
.card.visible{opacity:1;transform:translateY(0)}
.two-col{display:grid;grid-template-columns:1fr 1fr;gap:20px}
@media (max-width:640px){.two-col{grid-template-columns:1fr}}

.emotion-card{border-left:3px solid var(--primary)}
.emotion-card__label{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--primary);font-weight:700;margin-bottom:10px}
.emotion-card__text{font-size:15px;line-height:1.6}

.pullquote{font-size:24px;font-weight:700;line-height:1.4;padding:32px;border-left:3px solid var(--primary);background:var(--bg-card);border-radius:var(--r-md);margin:24px 0}

.palette-card{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--r-xl);overflow:hidden;margin-bottom:24px}
.palette-card__head{background:var(--bg-card-2);border-bottom:1px solid var(--border);padding:28px 32px}
.palette-card__head h3{font-size:20px;font-weight:800;letter-spacing:-0.01em;margin-bottom:6px}
.palette-card__head p{font-size:14px;color:var(--text-secondary);line-height:1.6}

.pillar-card{position:relative;padding-left:32px}
.pillar-card::before{content:'';position:absolute;left:0;top:28px;bottom:28px;width:3px;border-radius:2px;background:var(--primary)}
.pillar-card__name{font-size:18px;font-weight:800;color:var(--primary);margin-bottom:8px}
.pillar-card__rationale{color:var(--text-secondary);font-size:14px;line-height:1.6;margin-bottom:10px}
.pillar-card__stake{font-size:13px;color:var(--text-muted);font-style:italic;margin-bottom:10px}
.weight-bar{height:6px;border-radius:3px;background:var(--bg-card-3);overflow:hidden}
.weight-bar__fill{height:100%;background:var(--primary);border-radius:3px;width:0;transition:width 1s cubic-bezier(.34,1.56,.64,1)}

.radar-wrap{background:var(--bg-card);border:1px solid var(--border);border-radius:var(--r-xl);padding:40px;margin-bottom:24px;display:grid;grid-template-columns:1fr 1fr;gap:40px;align-items:center}
@media (max-width:640px){.radar-wrap{grid-template-columns:1fr}}
.radar-svg{width:100%;max-width:320px;overflow:visible}
.radar-grid{fill:none;stroke:var(--border-bright);stroke-width:.8}
.radar-axis{stroke:var(--border);stroke-width:.8}
.radar-area{fill:rgba(var(--primary-rgb),.18);stroke:var(--primary);stroke-width:2;filter:drop-shadow(0 0 8px rgba(var(--primary-rgb),.4))}
.radar-point{fill:var(--primary)}
.radar-label{font-family:var(--font);font-size:9px;font-weight:700;fill:var(--text-muted);letter-spacing:.06em;text-transform:uppercase}
.radar-legend-title{font-size:18px;font-weight:800;margin-bottom:4px}
.radar-legend-sub{font-size:13px;color:var(--text-secondary);margin-bottom:16px;line-height:1.6}

.calendar-row{position:relative;display:flex;gap:14px;align-items:flex-start;padding:16px 60px 16px 0;border-bottom:1px solid var(--border-bright)}
.calendar-row__num{color:var(--primary);font-weight:800;font-size:14px;min-width:24px}
.calendar-row__topic{flex:1;font-size:14px;line-height:1.5}
.chip{display:inline-block;font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.04em;padding:3px 10px;border-radius:var(--r-pill);margin-right:6px;margin-top:8px;background:rgba(var(--primary-rgb),.12);color:var(--primary-light)}
.copy-topic-btn{position:absolute;right:0;top:14px;background:rgba(var(--primary-rgb),0.1);border:1px solid rgba(var(--primary-rgb),0.3);color:var(--primary-light);font-family:var(--font);font-size:10px;font-weight:700;padding:6px 12px;border-radius:var(--r-pill);cursor:pointer;transition:all .18s ease;white-space:nowrap}
.copy-topic-btn:hover{background:rgba(var(--primary-rgb),0.22)}
.copy-topic-btn.copied{background:rgba(34,197,94,.15);border-color:rgba(34,197,94,.4);color:#22C55E}

.copy-toast{position:fixed;bottom:28px;left:50%;transform:translateX(-50%) translateY(20px);background:var(--bg-card-2);border:1px solid var(--border-bright);color:var(--text-primary);padding:12px 22px;border-radius:var(--r-pill);font-size:13px;font-weight:600;box-shadow:var(--shadow-lift);opacity:0;transition:opacity .25s ease,transform .25s ease;z-index:300;pointer-events:none}
.copy-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}

footer{text-align:center;padding:48px 24px;color:var(--text-muted);font-size:12px;border-top:1px solid var(--border)}
footer strong{color:var(--text-secondary)}
</style>
</head>
<body>

<button class="theme-toggle" onclick="toggleTheme()" title="Switch light/dark background" aria-label="Toggle theme">
  <svg class="icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.79A9 9 0 1111.21 3 7 7 0 0021 12.79z"/></svg>
  <svg class="icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>
</button>

<div class="cover">
  <div class="cover-particle" style="width:6px;height:6px;top:20%;left:15%;--dur:5s;--delay:0s;"></div>
  <div class="cover-particle" style="width:4px;height:4px;top:35%;left:80%;--dur:6.5s;--delay:1s;"></div>
  <div class="cover-particle" style="width:8px;height:8px;top:70%;left:25%;--dur:4.5s;--delay:2s;"></div>
  <div class="cover-particle" style="width:5px;height:5px;top:60%;left:65%;--dur:5.5s;--delay:1.5s;"></div>
  <div class="cover__eyebrow">Content Foundation · Purely Personal</div>
  <div class="cover__title">"[POSITIONING STATEMENT]"</div>
  <div class="cover__sub">Built for [NAME] · [DATE]</div>
</div>

<div class="stat-strip">
  <div class="stat-strip__inner">
    <div class="stat">
      <svg class="score-donut" viewBox="0 0 64 64">
        <circle class="donut-track" cx="32" cy="32" r="28"/>
        <circle class="donut-fill" data-offset="[OFFSET]" cx="32" cy="32" r="28" transform="rotate(-90 32 32)"/>
      </svg>
      <div class="stat__label">Positioning Strength</div>
    </div>
    <div class="stat"><div class="stat__num" data-count="[N]">0</div><div class="stat__label">Pillars</div></div>
    <div class="stat"><div class="stat__num" data-count="[N]">0</div><div class="stat__label">Topics</div></div>
    <div class="stat"><div class="stat__num" data-count="30">0</div><div class="stat__label">Days Planned</div></div>
  </div>
</div>

<nav class="nav-tabs">
  <div class="nav-tabs__inner">
    <button class="tab-btn active" onclick="switchTab('icp',this)"><span class="tab-btn__dot"></span>ICP &amp; Positioning</button>
    <button class="tab-btn" onclick="switchTab('messaging',this)"><span class="tab-btn__dot"></span>Messaging House</button>
    <button class="tab-btn" onclick="switchTab('ladder',this)"><span class="tab-btn__dot"></span>Positioning Ladder</button>
    <button class="tab-btn" onclick="switchTab('pillars',this)"><span class="tab-btn__dot"></span>Content Pillars</button>
    <button class="tab-btn" onclick="switchTab('calendar',this)"><span class="tab-btn__dot"></span>30-Day Calendar</button>
    <button class="tab-btn" onclick="switchTab('use',this)"><span class="tab-btn__dot"></span>How To Use This</button>
  </div>
</nav>

<div class="page">

  <div id="tab-icp" class="tab-panel active">
    <div class="section-title">Who You're Talking To</div>
    <div class="card">[ICP PARAGRAPH]</div>
    <div class="two-col">
      <div class="card emotion-card"><div class="emotion-card__label">The Fear</div><div class="emotion-card__text">[FEAR LINE]</div></div>
      <div class="card emotion-card"><div class="emotion-card__label">The Want</div><div class="emotion-card__text">[WANT LINE]</div></div>
    </div>
    <div class="pullquote">[POSITIONING STATEMENT]</div>
  </div>

  <div id="tab-messaging" class="tab-panel">
    <div class="section-title">Your Messaging House</div>
    <div class="palette-card">
      <div class="palette-card__head"><h3>Unique Value Proposition</h3><p>[UVP STATEMENT]</p></div>
    </div>
    <!-- repeat x3 -->
    <div class="card pillar-card">
      <div class="pillar-card__name">[PILLAR MESSAGE NAME]</div>
      <div class="pillar-card__rationale">[WHAT THIS PILLAR PROVES]</div>
      <div class="pillar-card__stake">Proof: [PROOF POINT]</div>
    </div>
  </div>

  <div id="tab-ladder" class="tab-panel">
    <div class="section-title">Positioning Ladder</div>
    <div class="two-col">
      <div class="card"><div class="pillar-card__name">Point of Difference</div><div class="pillar-card__rationale">[POD]</div></div>
      <div class="card"><div class="pillar-card__name">Point of Parity</div><div class="pillar-card__rationale">[POP]</div></div>
    </div>
    <div class="card">
      <div style="display:flex;align-items:center;gap:20px">
        <svg class="score-donut" viewBox="0 0 64 64">
          <circle class="donut-track" cx="32" cy="32" r="28"/>
          <circle class="donut-fill" data-offset="[OFFSET]" cx="32" cy="32" r="28" transform="rotate(-90 32 32)"/>
        </svg>
        <div><strong>Positioning Strength: [SCORE]/10</strong><div class="pillar-card__rationale">[WHAT MOVES THIS SCORE UP]</div></div>
      </div>
    </div>
  </div>

  <div id="tab-pillars" class="tab-panel">
    <div class="section-title">Your Content Pillars</div>
    <div class="radar-wrap">
      <div>
        <svg class="radar-svg" viewBox="0 0 320 320">
          <polygon class="radar-grid" points="160.0,30.0 283.6,119.8 236.4,265.2 83.6,265.2 36.4,119.8"/>
          <polygon class="radar-grid" points="160.0,56.0 258.9,127.9 221.1,244.1 98.9,244.1 61.1,127.9"/>
          <polygon class="radar-grid" points="160.0,82.0 234.2,135.9 205.8,223.1 114.2,223.1 85.8,135.9"/>
          <polygon class="radar-grid" points="160.0,108.0 209.5,143.9 190.6,202.1 129.4,202.1 110.5,143.9"/>
          <polygon class="radar-grid" points="160.0,134.0 184.7,152.0 175.3,181.0 144.7,181.0 135.3,152.0"/>
          <line class="radar-axis" x1="160" y1="160" x2="160.0" y2="30.0"/>
          <line class="radar-axis" x1="160" y1="160" x2="283.6" y2="119.8"/>
          <line class="radar-axis" x1="160" y1="160" x2="236.4" y2="265.2"/>
          <line class="radar-axis" x1="160" y1="160" x2="83.6" y2="265.2"/>
          <line class="radar-axis" x1="160" y1="160" x2="36.4" y2="119.8"/>
          <!-- radar-area points computed from each pillar's weight/10 along its axis; radar-point at each vertex -->
          <polygon class="radar-area" points="[COMPUTED FROM WEIGHTS]"/>
          <text class="radar-label" x="160.0" y="12.0" text-anchor="middle">[PILLAR 1]</text>
          <text class="radar-label" x="300.8" y="114.3" text-anchor="start">[PILLAR 2]</text>
          <text class="radar-label" x="247.0" y="279.7" text-anchor="start">[PILLAR 3]</text>
          <text class="radar-label" x="73.0" y="279.7" text-anchor="end">[PILLAR 4]</text>
          <text class="radar-label" x="19.2" y="114.3" text-anchor="end">[PILLAR 5 OR OMIT IF ONLY 4]</text>
        </svg>
      </div>
      <div>
        <div class="radar-legend-title">Where your content weight sits</div>
        <div class="radar-legend-sub">The pillar closest to your offer usually deserves the most weight. This shape shows whether your plan actually reflects that.</div>
      </div>
    </div>
    <!-- repeat per pillar -->
    <div class="card pillar-card">
      <div class="pillar-card__name">[PILLAR NAME]</div>
      <div class="pillar-card__rationale">[WHY ONLY THIS PERSON OWNS IT]</div>
      <div class="pillar-card__stake">[THE STAKE FOR THE READER]</div>
      <div class="weight-bar"><div class="weight-bar__fill" data-w="[WEIGHT*10]"></div></div>
    </div>
  </div>

  <div id="tab-calendar" class="tab-panel">
    <div class="section-title">Your Next 30 Days</div>
    <!-- repeat per topic — unique id per row: topic-01, topic-02, etc -->
    <div class="calendar-row">
      <div class="calendar-row__num">01</div>
      <div class="calendar-row__topic">
        <span id="topic-01">[TOPIC]</span>
        <div><span class="chip">[PILLAR]</span><span class="chip">[FORMAT]</span><span class="chip">[INTENT]</span></div>
      </div>
      <button class="copy-topic-btn" onclick="copyTopic(this,'topic-01')">Copy topic</button>
    </div>
  </div>

  <div id="tab-use" class="tab-panel">
    <div class="section-title">How To Use This</div>
    <div class="card">1. Post one topic at a time, in order or by mood, the sequencing already balances your pillars.</div>
    <div class="card">2. Don't rewrite these into generic captions. Feed each one into the Content Creator skill for the full post, hooks, and script.</div>
    <div class="card">3. Revisit this foundation every 90 days. Your ICP should sharpen, not change completely.</div>
  </div>

</div>

<footer>
  <p>Built by <strong>Purely Personal</strong></p>
  <p>Content Foundation · Marketing on Autopilot</p>
</footer>

<script>
function toggleTheme() {
  const html = document.documentElement;
  html.setAttribute('data-theme', html.getAttribute('data-theme') === 'dark' ? 'light' : 'dark');
}

function switchTab(id, btn) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-' + id).classList.add('active');
  btn.classList.add('active');
  window.scrollTo({ top: document.querySelector('.nav-tabs').offsetTop, behavior: 'smooth' });
}

function countUp(el, target, duration) {
  const start = performance.now();
  (function tick(now) {
    const p = Math.min((now - start) / duration, 1);
    el.textContent = Math.round(p * target);
    if (p < 1) requestAnimationFrame(tick); else el.textContent = target;
  })(performance.now());
}

/* ── ROBUST CLIPBOARD (works on file:// too) ────────── */
function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed';
  ta.style.left = '-9999px';
  document.body.appendChild(ta);
  ta.focus(); ta.select();
  let ok = false;
  try { ok = document.execCommand('copy'); } catch (e) { ok = false; }
  document.body.removeChild(ta);
  return ok;
}
function copyToClipboard(text) {
  return new Promise((resolve, reject) => {
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(resolve).catch(() => fallbackCopy(text) ? resolve() : reject());
    } else {
      fallbackCopy(text) ? resolve() : reject();
    }
  });
}
const toast = document.createElement('div');
toast.className = 'copy-toast';
document.body.appendChild(toast);
function showToast(msg) {
  toast.textContent = msg;
  toast.classList.add('show');
  clearTimeout(toast._t);
  toast._t = setTimeout(() => toast.classList.remove('show'), 2000);
}
function copyTopic(btn, id) {
  const el = document.getElementById(id);
  if (!el) return;
  copyToClipboard(el.innerText.trim()).then(() => {
    btn.classList.add('copied');
    const orig = btn.textContent;
    btn.textContent = 'Copied!';
    showToast('Topic copied, paste into Content Creator');
    setTimeout(() => { btn.classList.remove('copied'); btn.textContent = orig; }, 2000);
  }).catch(() => showToast('Copy blocked by browser, select the text manually'));
}

const revealObs = new IntersectionObserver(entries => {
  entries.forEach(e => {
    if (!e.isIntersecting) return;
    e.target.classList.add('visible');
    e.target.querySelectorAll('.weight-bar__fill[data-w]').forEach(b => setTimeout(() => b.style.width = b.dataset.w + '%', 150));
    revealObs.unobserve(e.target);
  });
}, { threshold: 0.1 });
document.querySelectorAll('.card').forEach(el => revealObs.observe(el));
document.querySelectorAll('[data-count]').forEach(el => countUp(el, +el.dataset.count, 900));
document.querySelectorAll('.donut-fill[data-offset]').forEach(el => setTimeout(() => el.style.strokeDashoffset = el.dataset.offset, 400));
</script>
</body>
</html>
```

### CALCULATING THE DONUT OFFSET

The donut circle has `stroke-dasharray:175` (roughly 2πr for r=28). For a score out
of 10: `offset = 175 - (175 * score / 10)`. Example: score 7/10 → offset ≈ 52.5.

### CALCULATING THE RADAR AREA POLYGON

Each pillar's weight (1-10) maps to a point along its axis, at `radius = 26 * weight
/ 10 * 5` (i.e. weight 10 reaches the outer ring at r=130, weight 5 reaches the
middle ring at r=65). Use these axis endpoints (already computed for 5 axes,
matching the grid above):

```
Axis 1 (top):     x = 160,             y = 160 - 130*(w/10)
Axis 2:           x = 160 + 130*(w/10)*0.951, y = 160 - 130*(w/10)*0.309
Axis 3:           x = 160 + 130*(w/10)*0.588, y = 160 + 130*(w/10)*0.809
Axis 4:           x = 160 - 130*(w/10)*0.588, y = 160 + 130*(w/10)*0.809
Axis 5:           x = 160 - 130*(w/10)*0.951, y = 160 - 130*(w/10)*0.309
```

If there are only 4 pillars, drop Axis 5 and recompute a 4-axis version using the
same method at 90 degree steps (0, 90, 180, 270) instead of 72 degree steps. Never
force a 5th pillar just to fill the chart.

---

## STEP 8 — DELIVERY

Save the file with `create_file`, present it with `present_files`. Filename pattern:
`content-foundation-[firstname].html`. No long postamble.

---

## QUALITY GATE

Before delivering, check:
- Zero mentions of "LinkedIn" unless the attendee named it as their only channel
- UVP is a testable claim, not a tagline
- POD is something a direct competitor genuinely doesn't say, not a generic strength
- Every pillar is a specific territory, not a generic label, and every weight is justified by how close that pillar sits to the actual offer
- Every one of the 20-24 topics is a real angle, not a placeholder
- All 6 tabs switch correctly, theme toggle works, cards animate on scroll, donut and radar animate in, weight bars fill to the right width
- Every topic row's copy button actually copies (test the fallback path mentally: this must work even opened directly from a downloaded file, not just from a server)
- No em dashes anywhere in the written copy

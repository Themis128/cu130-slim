---
name: kakkava-research-digest
description: >
  Digest of the remaining research material in OneDrive/Kakkava Sofia/Papers +
  Back 2 business — Calvanese et al. "Agentic Business Process Management: A
  Research Manifesto" (arXiv 2603.18916), Chernenko & Babenko "Data-driven
  trendwatching model: the Agentic AI case" (J. European Economy 2025),
  Shrivastva et al. "Promoting a Website with SEO using PPC" (ShodhKosh 2024),
  Adesina et al. "Leveraging predictive analytics for strategic
  decision-making" (2024), and the Business Scaling Checklist. Use when
  answering "which paper informed this", extending agentic-bpm governance,
  trendwatching decisions, or scaling/audit reviews.
---

# Kakkava Research Digest — remaining Papers + Scaling Checklist

Companion to `agentic-bpm` (Vu et al., practitioner perspective, already
adopted), `cross-channel-ops` (ADMAN 2024), `engagement-ops` (Kontigensi 2025).
This file captures the rest of the corpus so no source sits unread.

## 1. Agentic BPM Research Manifesto — Calvanese, Casciani, De Giacomo et al.
(arXiv 2603.18916, 2026)

Different lineage from `agentic-bpm`'s Vu et al. practitioner study — this is
the *formal research manifesto* framing agentic BPM as an open discipline:
agents as first-class process participants whose goals, plans, and learning
must be modeled explicitly, not treated as opaque task workers.

Adopted deltas for SocialAuto:
- **Model the agent's goal & plan, not just the task.** Our worker roster
  (`workforce_audit.py` goal/guardrails/autonomy/fallback contract) already
  does this — the manifesto validates the design; keep per-worker goal text
  specific, not generic "post things".
- **Process-aware agents** beat task-aware ones — workers should see the
  campaign/funnel state (we do via publish_queue + monetization data), not
  just their queue item.
- **Learning loops need bounded adaptation** — failure-learning sweeps +
  engagement_audit already close the loop; keep adaptation inside declared
  autonomy bounds.

## 2. Data-Driven Trendwatching Model: the "Agentic AI" case —
Chernenko & Babenko (J. European Economy 24(4), 2025)

Integrated trendwatching model combining web analytics with foresight scenario
modelling; argues agentic AI is what makes continuous trendwatching feasible
for SMEs (not just corporations with R&D budgets).

Adopted deltas:
- **Trendwatching is a continuous loop**, not a weekly brief — matches the
  data-driven mandate (live trend queries per post) and the scheduled
  strategy briefs; keeps momentum-grounded topics current.
- Agentic trendwatching for SMEs is our exact value proposition to cite in
  Cloudless marketing content (proof the stack's approach is literature-
  backed).

## 3. Promoting a Website with SEO using PPC — Shrivastva, Gautam, Kar
(ShodhKosh 5(5), 2024)

Low-rigor practitioner piece; its one durable claim: paid search and organic
SEO compound — PPC keyword data (CTR/convert terms) should feed organic SEO
targeting and vice versa.

Adopted delta: ad-campaign keyword/copy learnings from the LinkedIn credit
campaign SHOULD inform organic post topics (share ONE utm_campaign slug —
already the cross-channel rule; this adds the keyword-learning channel).

## 4. Predictive Analytics for Strategic Decision-Making —
Adesina, Iyelolu, Paul (2024)

Generic literature review; durable claims: predictive analytics pays when it
drives a specific recurring decision (not a dashboard), and KPIs must be
instrumented before models help.

Adopted delta: our engagement/conversion analytics exist to feed decisions
(cadence, platform budget, YES-Y leg selection) — keep analytics outputs
decision-shaped per the data-driven mandate, not vanity metrics.

## 5. Business Scaling Checklist (Back 2 business PDF)

Operational scaling self-audit. Key adoption: use as the monthly scaling
review scaffold — check offer clarity, funnel stage coverage (YES-Y legs),
delivery capacity, and tracking before increasing ad spend or channel count.
Reference source: `OneDrive/Kakkava Sofia/Back 2 business/📈 Business Scaling
Checklist.pdf` — consult the PDF directly for the full itemized list; this
note exists so the checklist is acknowledged, not summarized.


## 6. Sustainability Agency Theory — Yang, Gao & Huang (Sustainability
17:4778, 2025)

Extends agency theory to hybrid orgs: agents must balance profit AND the
principal's mission; conflicts surface when agents drift to profit-only
actions. Adopted for the agency pipeline: client-facing agent reports carry
verifiable evidence (PM updates get a `Verify:` artifact line; handoffs get
a self-verification section) — transparency sustains the principal-agent
relationship.

## 7. Generative AI & DevOps Pipelines — Joshi (IJIRCST 13(4), 2025)

Review of agentic CI/CD: agents propose changes as PRs, CI validates,
humans merge, progressive delivery, documented rollback. Adopted as the
`agency-engineer` worker: drafts PR manifests (branch/files/tests/rollback)
for sent proposals — plans only, codegen stays at `review` tier until the
autonomy ladder graduates it.


## 8. LinkedIn SMB Playbook — "The Smarter Way to Scale Your Small
Business" (v3pdf, LinkedIn internal collateral)

LinkedIn's own SMB advertising playbook: Crawl–Walk–Run framework —
organic foundation (complete page, weekly posts, learn what resonates) →
boost validation (small capped budgets on proven posts) → campaign scale
(matched audiences, funnel ads, CRM-linked revenue measurement). Adopted
as the `linkedin_growth` service in `agency_catalog.py` (€900–1,800): the
three stages ship as named `Service.phases` shown in proposals, mapped to
the 14-day window (days 1-5 / 6-10 / 11-14). Spend-gates between stages
match the no-spend frame — boosts/campaigns stay human-approved, never
autonomous. Note: the file itself is LinkedIn's collateral — adopt the
framework, do not redistribute the PDF as Cloudless material.


## 9. The Influencer-Entrepreneurship Journey — Cheng, de Regt & Young
(Psychology and Marketing 41, 2024, doi:10.1002/mar.22084)

Five-stage model of the influencer journey: potential → formulation →
competence → monetization → reward, each with distinct attentional capital
and brand-engagement guidance (brands must match collab type to stage).
Adopted as `app/services/influence_stage.py`: deterministic
`classify_stage(followers, engagement_rate, paid_collabs, recurring_offers,
own_products)` returning stage + playbook actions. Uses: (a) Visibility-Era
journey gating — our own Sofia-brand stage determines which paid moves are
earned; (b) agency growth audits — partner/client stage informs the offer
(gifted vs paid vs partnership). Pairs with the Crawl–Walk–Run paid-side
framework: Crawl≈potential→formulation, Walk≈competence,
Run≈monetization→reward.

# Offer countdown token (`{countdown}`)

Every post for a time-boxed campaign (Black Friday, launches, flash sales)
can carry a live days/hours/minutes countdown. The token is expanded at
publish time — never at generation time — so the number the reader sees is
always current even when the post sits in the queue for days.

## Token forms

| Form | Deadline source |
|---|---|
| `{countdown:2026-11-27T23:59:00+02:00}` | the ISO-8601 deadline in the token |
| `{countdown}` (bare) | `post.meta_data["offer_ends_at"]` |

- Naive ISO datetimes are read as UTC — always include an offset.
- A past deadline renders as `expired`.
- An unparseable token is left in the text untouched (publish still works).

Example caption:

```
Only {countdown:2026-11-27T23:59:00+02:00} left — the offer ends Friday.
```

publishes as `Only 5 days, 19 hours, 19 minutes left — the offer ends Friday.`

## Where it expands

| Lane | File | When |
|---|---|---|
| Social posts | `content_renderer.render_post_text` | every publish (publish, retry, scheduled, preview) |
| Cross-posts | `api/content.cross_post_to_platform` | token stays raw + `offer_ends_at` is copied to the new post, so the cross-post keeps its own live countdown |
| Email campaigns | `email_campaigns.build_bodies` | at send, using `campaign.meta_data["offer_ends_at"]` |

`render_post_text(..., expand_countdown=False)` keeps tokens raw — used for
persisted/scheduled text so a copy always re-renders live.

## Getting the token into generated posts

`COUNTDOWN_RULE` in `app/services/content_guides.py` is injected into every
generation prompt (both the standard and the `saved_template` path). When the
brief names a deadline, the model emits the token verbatim. For manual posts
just type the token into the text editor.

## Setting `offer_ends_at` on an existing post or campaign

```bash
python3 .devin/skills/offer-countdown-ops/scripts/offer_countdown.py \
    set <post-uuid> 2026-11-27T23:59:00+02:00
python3 .devin/skills/offer-countdown-ops/scripts/offer_countdown.py \
    preview "Only {countdown:2026-11-27T23:59:00+02:00} left"
```

(Mirrored at `.cursor/skills/offer-countdown-ops/scripts/offer_countdown.py`.)

## Note on already-published posts

Captions on published media are static — the countdown they contain is a
snapshot. For a live countdown on an already-posted item, edit the caption
(Graph API does not support it; use the `ig-takedown-ops` `ig_edit.py`
instagrapi lane for Instagram). Put the explicit end date in the text too so
the post stays accurate after the snapshot ages.

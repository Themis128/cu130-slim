# Funnel email layout (branded HTML)

Every funnel/lifecycle email is sent as `multipart/alternative`. The text part
is the plain-text copy, and the HTML part is generated from that same copy by
`app/services/funnel_email.py`. The layout matches the approved
"free tech checkup" reference: table layout, inline CSS, 600px wide, no JS,
a header, a body card, a CTA button, a signature and a plain-link fallback footer.

## Which senders use it

| Sender | Module | CTA |
|---|---|---|
| Nurture ladder + quiet-lead lane | `nurture_sequence.py` (`STEPS`, `QUIET_STEPS` → `cta`) | first URL in the body |
| Playbook delivery | `playbook_email.py` | Download PDF + Book the audit |
| Proposal send | `agency_pipeline.send_proposal` | payment link, else consultation |
| Proposal follow-ups | `proposal_followup.py` (`cta`) | first URL in the body |
| Kickoff, intake nudge, post-consult, testimonial, delight ladder | `agency_pipeline.py` | none (links stay inline) |
| Billing owner mails | `api/billing._notify_team_owner` | none |
| Transactional (welcome, reset, invites…) | `email_templates._html_wrapper` → `inline_fragment` | `class="btn"` anchors |
| Mautic win-back | `scripts/mautic_winback_setup.py` (`customHtml` + `plainText`) | first URL |

Campaigns/drips (`email_campaigns`, `email_drip`) keep `email_brand`.

## Adding or editing a funnel email

1. Write the copy as plain text. You can use paragraphs, `- ` or `1. ` lists,
   `**bold**`, `# heading`, `---`, and a `— Name, Org` signature line.
2. Send it with
   `send_email(subject=s, text_body=body, html_body=render_funnel_html(body, subject=s, cta_label="Book…"))`.
   - With `cta_label`, the body's first `http(s)` URL becomes the primary button.
   - If you need specific links, pass `ctas=[Cta(label, url), …]` instead. The first is
     primary and the rest are outlined. A bare URL or a `[text](url)` link in the
     copy is replaced by its button.
3. Merge values are HTML-escaped automatically. Mautic/ESP tokens like
   `{contactfield=firstname}` are left untouched.
4. Add a test asserting `html_body` is set (see `tests/unit/test_funnel_email.py`).

## Preview locally (no sending)

```bash
cd social-automation/backend && . .venv/bin/activate
python -c "from app.services.funnel_email import render_funnel_html as r; \
print(r(open('body.txt').read(), subject='Test', cta_label='Book'))" > /tmp/preview.html
```

Open `/tmp/preview.html` in a browser.

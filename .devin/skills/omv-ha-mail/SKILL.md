---
name: omv-ha-mail
description: >-
  Send and manage email through the self-hosted Cloudless mail stack on
  omv-ha (postfix relay via Resend + dovecot IMAP). Use when sending
  transactional email, checking mail logs, verifying mailbox auth,
  testing SMTP connectivity, or sending emails with attachments from
  the cloudless.gr domain.
allowed-tools:
  - read
  - exec
  - grep
  - glob
  - write
  - edit
triggers:
  - user
  - model
---

# omv-ha Self-Hosted Mail

Send and manage email through the Cloudless self-hosted mail stack on
`omv-ha` (Pi 4, 192.168.1.130 / 100.95.117.84 via Tailscale).

## Architecture

```
Sender (this machine / container)
  → omv-ha postfix :587 (SASL auth, STARTTLS)
  → smtp.resend.com:587 (Resend relay)
  → recipient MX

Inbound:
  Cloudflare Email Routing → mail-ingest Worker → omv-ha dovecot LMTP
  → Maildir /var/mail/vhosts/cloudless.gr/tbaltzakis/
```

- **postfix**: relay-only via `smtp.resend.com:587`, no direct port 25
  (Starlink CGNAT blocks it). Submission on :587 requires SASL auth.
- **dovecot**: virtual Maildir mailbox, IMAP + LMTP. Mailbox:
  `tbaltzakis@cloudless.gr`.
- **Resend**: outbound relay. `cloudless.gr` is a verified Resend domain.
  The Resend API key is stored in `/etc/postfix/sasl_passwd` on omv-ha
  (not in any repo env file).
- **No port 25**: omv-ha is behind Starlink CGNAT; port 25 is blocked.
  All outbound goes through Resend.

## Connection details

| Parameter | Value |
|-----------|-------|
| SMTP host | `192.168.1.130` (LAN) or `100.95.117.84` (Tailscale) |
| SMTP port | `587` (submission, STARTTLS) |
| IMAP host | `192.168.1.130` (LAN) or `100.95.117.84` (Tailscale) |
| IMAP port | `993` (IMAPS) |
| Auth | SASL PLAIN/LOGIN via dovecot |
| Username | `tbaltzakis@cloudless.gr` |
| Password | mailbox password (set during `setup-mail-server.sh`) |
| From | `tbaltzakis@cloudless.gr` |
| Relay | `smtp.resend.com:587` |
| Trusted networks | `127.0.0.0/8`, `100.64.0.0/10` (Tailscale), `192.168.1.0/24` (LAN) |

## Security

- **Never** store the mailbox password or Resend API key in repo files,
  env files, or documentation. The password is set during
  `setup-mail-server.sh` and stored in `/etc/dovecot/users` on omv-ha.
- **Never** print credentials in logs or tool output.
- The Resend API key lives in `/etc/postfix/sasl_passwd` on omv-ha only.
- SMTP submission requires STARTTLS + SASL auth — no open relay.

## Sending email

### Quick send (Python SMTP)

```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

msg = MIMEMultipart()
msg['From'] = 'tbaltzakis@cloudless.gr'
msg['To'] = 'recipient@example.com'
msg['Subject'] = 'Subject here'
msg.attach(MIMEText('Body text here', 'plain'))

server = smtplib.SMTP('192.168.1.130', 587, timeout=30)
server.starttls()
server.login('tbaltzakis@cloudless.gr', 'MAILBOX_PASSWORD')
server.sendmail('tbaltzakis@cloudless.gr', 'recipient@example.com', msg.as_string())
server.quit()
```

### Send with attachment

See `scripts/send-mail.sh` — accepts subject, body, recipient, and
optional file attachment path.

### Send via skill script

```bash
# Simple email
.devin/skills/omv-ha-mail/scripts/send-mail.sh \
  --to "recipient@example.com" \
  --subject "Subject" \
  --body "Body text"

# Email with attachment
.devin/skills/omv-ha-mail/scripts/send-mail.sh \
  --to "recipient@example.com" \
  --subject "Subject" \
  --body "Body text" \
  --attach "/path/to/file.pdf"
```

## Tool scripts

```bash
# Send email (with optional attachment)
.devin/skills/omv-ha-mail/scripts/send-mail.sh

# Test SMTP connectivity to omv-ha
.devin/skills/omv-ha-mail/scripts/test-smtp.sh

# Check mailbox auth (verify credentials work)
.devin/skills/omv-ha-mail/scripts/check-mailbox.sh

# Check mail queue on omv-ha (requires SSH access)
.devin/skills/omv-ha-mail/scripts/mail-queue.sh

# Read inbox via IMAP (list recent messages)
.devin/skills/omv-ha-mail/scripts/read-inbox.sh
```

## Setup references

- Mail server install: `cloudless.gr/infrastructure/omv-ha/setup-mail-server.sh`
- Submission enable: `cloudless.gr/infrastructure/omv-ha/enable-mail-submission.sh`
- Inbound routing: `cloudless.gr/infrastructure/omv-ha/mail-ingest/`
- Architecture doc: `cloudless.gr/docs/MAIL-SERVER-SETUP.md`

## Limitations

- No SSH key is set up from this machine to omv-ha by default. Scripts
  that require SSH (`mail-queue.sh`) will prompt for password or use
  an existing key if configured.
- Port 25 is blocked (Starlink CGNAT) — all outbound goes via Resend.
- The mailbox password must be provided by the user or read from a
  secure source. It is not stored in any repo or env file.
- Resend free tier has a sending limit (3,000 emails/month, 100/day).

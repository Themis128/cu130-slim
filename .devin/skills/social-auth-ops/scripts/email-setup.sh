#!/usr/bin/env bash
# email-setup.sh <resend_api_key>
# Sets SMTP_PASSWORD (Resend SMTP = api key as password), restarts, test-sends.
set -euo pipefail
cd "$(git rev-parse --show-toplevel)"
KEY="${1:?usage: email-setup.sh <resend_api_key>}"
grep -qE '^SMTP_PASSWORD=' .env && sed -i "s|^SMTP_PASSWORD=.*|SMTP_PASSWORD=$KEY|" .env || echo "SMTP_PASSWORD=$KEY" >> .env
echo "SMTP_PASSWORD set. Restarting…"
docker compose up -d --force-recreate social-api social-worker-publishing >/dev/null 2>&1 || true
sleep 8
echo "Test send:"
docker compose exec -T social-api python - <<'PY'
import smtplib, os
s = smtplib.SMTP(os.environ.get("SMTP_HOST","smtp.resend.com"), int(os.environ.get("SMTP_PORT","587")))
s.starttls(); s.login("resend", os.environ["SMTP_PASSWORD"])
s.sendmail(os.environ.get("SMTP_FROM","noreply@cloudless.gr"), "reviewer@cloudless.gr",
           "Subject: SocialAuto email OK\n\nSMTP is working.")
print("test email sent")
PY

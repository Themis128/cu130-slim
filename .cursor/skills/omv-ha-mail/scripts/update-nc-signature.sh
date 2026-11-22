#!/usr/bin/env bash
# update-nc-signature.sh — push the Cloudless signature into every
# Nextcloud Mail account (oc_mail_accounts.signature) on omv.
# Runs over SSH using the same OMV_SSH_KEY pattern as the CI shim:
#   ssh tbaltzakis@100.74.191.58 → docker exec nextcloud-db psql
# Nextcloud Mail stores the per-account HTML signature in
# oc_mail_accounts.signature — one UPDATE covers every account/alias.
set -euo pipefail
SIG_FILE="$(dirname "$0")/signature.html"
[ -f "$SIG_FILE" ] || { echo "signature.html not found next to script"; exit 1; }

python3 - "$SIG_FILE" <<'PY' | ssh -o BatchMode=yes tbaltzakis@100.74.191.58 "sudo docker exec -i nextcloud-db psql -U nextcloud -d nextcloud"
import sys
html = open(sys.argv[1]).read().replace("'", "''")
print(f"UPDATE oc_mail_accounts SET signature = '{html}' WHERE 1=1;")
print(f"UPDATE oc_mail_aliases   SET signature = '{html}' WHERE 1=1;")
PY
echo "signature pushed to Nextcloud Mail accounts"

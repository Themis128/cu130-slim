#!/bin/bash
# reset-user-password.sh — reset an authentik user's password via ak shell (k3s).
# Usage: reset-user-password.sh <username>   (prompts for new password, never echoed/arg)
set -euo pipefail
U=${1:?usage: reset-user-password.sh <username>}
read -srp "new password for $U: " PW; echo
kubectl -n authentik exec -i deploy/authentik-server -- ak shell <<EOF
from authentik.core.models import User
u=User.objects.get(username='$U')
u.set_password('$PW')
u.save()
print("password reset for", u.username)
EOF

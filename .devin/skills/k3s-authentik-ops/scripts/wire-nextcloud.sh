#!/bin/bash
set -euo pipefail
F=/home/tbaltzakis/.config/secrets/oidc-nextcloud
. "$F"
H=${1:-100.74.191.58}
CT=nextcloud-aio-nextcloud
for kv in "providerid authentik" "clientid $client_id" "clientsecret $client_secret" "discoveryuri ${issuer}.well-known/openid-configuration" "unique_id sub" "scope openid profile email groups" "providername authentik"; do
  ssh -o BatchMode=yes tbaltzakis@$H "docker exec -i $CT occ --no-ansi config:app:set user_oidc $(echo $kv | cut -d\  -f1) --value=\"$(echo $kv | cut -d\  -f2-)\""
done
echo "nextcloud wired"

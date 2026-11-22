#!/bin/bash
# create_admin_token.sh — mint an authentik admin API token on the server host via SSH.
# Usage: bash create_admin_token.sh [ssh-host]  (default omv: tbaltzakis@192.168.1.23)
HOST=${1:-tbaltzakis@192.168.1.23}
ssh "$HOST" docker
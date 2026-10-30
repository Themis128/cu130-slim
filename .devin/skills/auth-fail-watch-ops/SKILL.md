---
name: auth-fail-watch-ops
description: Authentik login_failed analyzer — groups by username/IP/UA.
  Own-IP + your-usernames + one-UA = stale saved credential retry; many IPs =
  external stuffing. Tool: scripts/auth_fail_watch.py [hours].
---
# Auth-fail-watch ops
Detects self-inflicted stale-credential retries vs real attacks.

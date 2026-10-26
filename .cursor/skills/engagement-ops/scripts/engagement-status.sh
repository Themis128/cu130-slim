#!/usr/bin/env bash
# engagement pipeline status — candidates, actions, sidecar cooldown
set -eu
cd /home/tbaltzakis/cu130-slim 2>/dev/null || cd /mnt/c/Users/tbaltzakis/cu130-slim
docker compose exec -T social-postgres psql -U social_user -d social_automation -t -A -F" | " -c "
select 'candidates', count(*), count(*) filter (where not scored) || ' unscored' from engagement_candidates
union all
select 'actions:'||status, count(*), '' from engagement_actions group by status order by 1"
docker compose exec -T social-api python3 -c "
import httpx
h=httpx.get('http://linkedin-browser-sidecar:9225/health',timeout=10).json()
print('li-sidecar:', 'rate_limited' if h.get('rate_limited') else 'ready', '| session:', h.get('has_session'))"

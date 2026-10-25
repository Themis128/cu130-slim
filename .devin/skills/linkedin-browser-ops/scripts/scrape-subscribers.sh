#!/usr/bin/env bash
# Scrape newsletter subscribers from the live page — the DOM path that
# works (the voyager endpoint is unreachable from our call shapes).
# usage: scrape-subscribers.sh <seriesUrn>
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python /dev/stdin "$1" <<'PYEOF'
import asyncio, httpx, os, sys, json
async def m(urn):
    url = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    series_id = urn.split(":")[-1]
    async with httpx.AsyncClient(base_url=url, timeout=180) as c:
        # 1. full-page navigation via the real endpoint — location.href=
        #    inside eval kills the JS context mid-call.
        await c.post("/debug/navigate", json={"url":
            f"https://www.linkedin.com/analytics/newsletter/urn:li:fsd_contentSeries:{series_id}/"})
        await asyncio.sleep(7)
        # 2. one eval: click "See all" → wait → scroll all → extract rows
        expr = """const link=[...document.querySelectorAll('a')].find(a=>
  /^\\s*See all\\s*$/i.test(a.innerText||'') && /subscriber/i.test(a.href));
if(!link) return {err:'no See all link', url:location.href,
  text:(document.body.innerText||'').slice(0,300)};
link.click();
await new Promise(r=>setTimeout(r,5000));
for(let i=0;i<25;i++){
  const dlg=[...document.querySelectorAll('*')].find(e=>
    e.scrollHeight>e.clientHeight&&e.clientHeight>300);
  if(dlg) dlg.scrollTop=dlg.scrollHeight;
  await new Promise(r=>setTimeout(r,700));
}
const rows=[...document.querySelectorAll('a[href*="/in/"]')];
const seen=new Set(); const subs=[];
for(const a of rows){
  const name=(a.innerText||'').split('\\n')[0].trim();
  const urn=(a.href.match(/\\/in\\/([\\w-]+)/)||[])[1]||'';
  if(!name||seen.has(urn)) continue; seen.add(urn);
  const parent=a.closest('li,div[class]');
  const txt=(parent?parent.innerText:'').split('\\n')
    .map(x=>x.trim()).filter(Boolean);
  const headline=txt.slice(1).join(' | ')
    .replace(/\\d+(st|nd|rd|th) degree connection \\| \\d+(st|nd|rd|th) \\| /,'')
    .replace(/\\| (Message|Follow)$/,'').slice(0,150);
  subs.push({name, urn, headline});
}
return {url:location.href, count:subs.length, subs:subs};"""
        r = await c.post("/debug/eval", json={"expr": expr})
        print(json.dumps(r.json().get("value"), indent=1)[:4000])
asyncio.run(m(sys.argv[1] if len(sys.argv)>1 else ""))
PYEOF

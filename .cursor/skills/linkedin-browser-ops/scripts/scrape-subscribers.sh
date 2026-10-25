#!/usr/bin/env bash
# Scrape ALL newsletter subscribers — count-based convergence (stops when
# the captured count stops growing, not when the dialog stops growing).
# Outputs {count, target, subs:[{name,urn,headline}]} as JSON on stdout.
# usage: scrape-subscribers.sh <seriesUrn>
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python /dev/stdin "$1" <<'PYEOF'
import asyncio, httpx, os, sys, json
async def m(urn):
    url = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    sid = urn.split(":")[-1]
    async with httpx.AsyncClient(base_url=url, timeout=300) as c:
        await c.post("/debug/navigate", json={"url":
            f"https://www.linkedin.com/analytics/newsletter/urn:li:fsd_contentSeries:{sid}/"})
        await asyncio.sleep(7)
        expr = """const link=[...document.querySelectorAll('a')].find(a=>
  /^\\s*See all\\s*$/i.test(a.innerText||'') && /subscriber/i.test(a.href));
if(!link) return {err:'no See all link', url:location.href,
  text:(document.body.innerText||'').slice(0,300)};
link.click();
await new Promise(r=>setTimeout(r,5000));

const target=parseInt(((document.body.innerText.match(/(\\d[\\d,]*)\\s+Subscriber/i)||[])[1]||'0').replace(/,/g,''),10)||null;

// count-based convergence: rows swap in as you scroll — height alone
// lies. Stop when captured count stops growing for 4 straight passes.
const extract=()=>{
  const rows=[...document.querySelectorAll('a[href*="/in/"]')];
  const seen=new Set(); const subs=[];
  for(const a of rows){
    const name=(a.innerText||'').split('\\n')[0].trim();
    const urn=(a.href.match(/\\/in\\/([\\w-]+)/)||[])[1]||'';
    if(!name||seen.has(urn)) continue; seen.add(urn);
    const parent=a.closest('li,div[class]');
    const txt=(parent?parent.innerText:'').split('\\n').map(x=>x.trim()).filter(Boolean);
    const headline=txt.slice(1).join(' | ')
      .replace(/\\d+(st|nd|rd|th) degree connection \\| \\d+(st|nd|rd|th) \\| /,'')
      .replace(/\\| (Message|Follow)$/,'').slice(0,150);
    subs.push({name, urn, headline});
  }
  return subs;
};

let prev=0,stable=0;
for(let i=0;i<80&&stable<4;i++){
  const dlg=[...document.querySelectorAll('*')].filter(e=>
    e.scrollHeight>e.clientHeight&&e.clientHeight>200);
  for(const d of dlg) d.scrollTop=d.scrollHeight;
  await new Promise(r=>setTimeout(r,800));
  const n=extract().length;
  if(n===prev){stable++;}else{stable=0;prev=n;}
  if(target&&n>=target) break;
}
const subs=extract();
return {url:location.href, target, count:subs.length, subs:subs};"""
        r = await c.post("/debug/eval", json={"expr": expr})
        print(json.dumps(r.json().get("value"), indent=1)[:6000])
asyncio.run(m(sys.argv[1] if len(sys.argv)>1 else ""))
PYEOF

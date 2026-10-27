#!/usr/bin/env bash
# Arm a persistent XHR+fetch sniffer inside the sidecar's live LinkedIn page.
# Captures survive SPA navigation via sessionStorage.
# usage: sniffer-install.sh
set -eu
cd /home/tbaltzakis/cu130-slim
docker compose exec -T social-api python /dev/stdin <<'PYEOF'
import asyncio, httpx, os
SNIFF = r"""
if(!window.__snifferArmed){window.__snifferArmed=1;
const log=(o)=>{try{const s=sessionStorage.getItem('__sniff')||'[]';sessionStorage.setItem('__sniff',JSON.stringify(JSON.parse(s).concat(o).slice(-200)))}catch(e){}};
const _f=window.fetch;
window.fetch=function(...a){
  const url=String(a[0]&&a[0].url?a[0].url:a[0]);
  return _f.apply(this,arguments).then(r=>{
    try{const c=r.clone();c.text().then(t=>log({type:'fetch',url,status:r.status,body:t.slice(0,600)}))}catch(e){}
    return r;});};
const _o=XMLHttpRequest.prototype.open,_s=XMLHttpRequest.prototype.send;
XMLHttpRequest.prototype.open=function(m,u){this.__u=u;return _o.apply(this,arguments)};
XMLHttpRequest.prototype.send=function(){const self=this;
  this.addEventListener('load',()=>{try{log({type:'xhr',url:String(self.__u),status:self.status,body:String(self.responseText||'').slice(0,600)})}catch(e){}});
  return _s.apply(this,arguments)};
}
return 'sniffer armed (fetch+xhr, sessionStorage-persistent)';
"""
async def m():
    url = os.environ.get("LINKEDIN_BROWSER_SIDECAR_URL", "http://stack-ops:9225")
    async with httpx.AsyncClient(base_url=url, timeout=60) as c:
        r = await c.post("/debug/eval", json={"expr": SNIFF})
        print(r.json().get("value"))
asyncio.run(m())
PYEOF

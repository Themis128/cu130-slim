#!/usr/bin/env python3
# update_app_meta.py --env <path> --app <slug> --note "..." [--token-env AUTHENTIK_ADMIN_TOKEN]
import json,sys,urllib.request,argparse
p=argparse.ArgumentParser(); p.add_argument('--env',required=True); p.add_argument('--app',required=True); p.add_argument('--note',required=True); p.add_argument('--token-env',default='AUTHENTIK_ADMIN_TOKEN'); a=p.parse_args()
env=dict(l.split('=',1) for l in open(a.env) if '=' in l and not l.startswith('#'))
tok=env.get(a.token_env,'').strip()
if not tok:
    has_outpost=any('AUTHENTIK' in k and 'TOKEN' in k for k in env)
    print('NO ADMIN TOKEN. Create in authentik admin: Directory -> Tokens & App passwords -> Create -> Intent: API Token, user=admin. Add as '+a.token_env+' to '+a.env); sys.exit(2)
def req(path,method='GET',body=None):
    r=urllib.request.Request('https://auth.cloudless.gr'+path,method=method,data=json.dumps(body).encode() if body else None,headers={'Authorization':'Bearer '+tok,'Content-Type':'application/json'})
    return json.load(urllib.request.urlopen(r,timeout=20))
apps=[x for x in req('/api/v3/core/applications/')['results'] if a.app in x.get('slug','')]
if not apps: print('app not found:',a.app); sys.exit(1)
req('/api/v3/core/applications/'+apps[0]['slug']+'/','PATCH',{'meta_description':a.note})
print('updated:',apps[0]['slug'])

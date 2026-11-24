"""LinkedIn archive/snapshot → normalized lake zone (lake/socialauto-linkedin-export/).

Reads /tmp/li_archive.zip (LinkedIn data export) and/or /tmp/li_snapshot.json,
converts every CSV/JSON surface into JSONL rows, uploads to the datalake
bucket so datalake-insights / contact-harvest / funnel lanes can query them.
"""
import asyncio, csv, io, json, sys, time, zipfile
sys.path.insert(0,'/app')
from app.services import r2_storage

BUCKET='datalake-bucket'
ZONE='lake/socialauto-linkedin-export'
STAMP=time.strftime('%Y-%m-%d',time.gmtime())

async def put(name, rows):
    data=('\n'.join(json.dumps(r,default=str) for r in rows)).encode()
    key=f'{ZONE}/{STAMP}/{name}.jsonl'
    r=await r2_storage.upload_object(key,data,content_type='application/jsonl',bucket=BUCKET)
    return key,len(rows),r.get('etag','')[:10]

def norm(name,row):
    # light PII mask: keep name/company/domain, hash raw emails
    for k in list(row.keys()):
        kl=k.lower()
        if 'email' in kl and '@' in str(row[k]):
            import hashlib
            row['email_domain']=str(row[k]).split('@')[-1]
            row['email_sha']='sha256:'+hashlib.sha256(row[k].encode()).hexdigest()[:12]
            del row[k]
    return {'_src':'linkedin-export','_table':name,'_ingested_at':time.strftime('%FT%TZ',time.gmtime()),**row}

async def main():
    done=[]
    try:
        z=zipfile.ZipFile('/tmp/li_archive.zip')
        for n in z.namelist():
            if not n.lower().endswith('.csv'): continue
            table=n.rsplit('/',1)[-1].replace('.csv','').replace(' ','_').lower()
            try:
                rows=list(csv.DictReader(io.StringIO(z.read(n).decode('utf-8','replace'))))
            except Exception: continue
            rows=[norm(table,dict(r)) for r in rows]
            if rows:
                done.append(await put(table,rows))
    except FileNotFoundError:
        pass
    try:
        snap=json.load(open('/tmp/li_snapshot.json'))
        if 'messages' in snap and isinstance(snap['messages'].get('conversations'),list):
            rows=[norm('inbox_threads',{'name':c.get('name',''),'preview':c.get('preview','')}) for c in snap['messages']['conversations']]
            done.append(await put('inbox_threads',rows))
        if 'permitted_services' in snap:
            txt=snap['permitted_services'].get('result','')
            svcs=[s.strip() for s in txt.split('\n') if s.strip() and 'Remove' not in s and 'Show more' not in s]
            rows=[norm('permitted_services',{'service':s}) for s in svcs if not s[0].isdigit() and len(s)<80 and not s.endswith(('GMT','UTC'))]
            done.append(await put('permitted_services',rows))
        if 'profile' in snap and 'profile' in snap['profile']:
            done.append(await put('profile',[norm('profile',snap['profile']['profile'])]))
    except FileNotFoundError:
        pass
    # manifest
    if done:
        manifest=[{'_src':'linkedin-export','zone':ZONE,'stamp':STAMP,'tables':[{'key':k,'rows':n} for k,n,_ in done]}]
        await put('_manifest',manifest)
    print(json.dumps({'ingested':[(k,n) for k,n,_ in done]}))
asyncio.run(main())

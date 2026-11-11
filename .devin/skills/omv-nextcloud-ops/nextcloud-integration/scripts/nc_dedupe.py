#!/usr/bin/env python3
"""Conservative Nextcloud dedupe: canonical InstantUpload/Camera vs dup dirs.
- propfind with oc:checksums (server-side SHA1) — no bulk download needed
- identical (name+size+sha1) -> DELETE from dup dir
- anything else -> MOVE to Conflicts-Archive/Dedupe-<date>/<orig-relative-path>
"""
import re,sys,urllib.request,urllib.parse,xml.etree.ElementTree as ET,hashlib,time
from pathlib import Path

env={}
for l in open('/home/tbaltzakis/cu130-slim/.env'):
    if '=' in l and not l.startswith('#'):
        k,v=l.split('=',1); env[k.strip()]=v.strip().strip('"').strip("'")
BASE=env['NEXTCLOUD_DAV_URL'].rstrip('/')
AUTH='Basic '+__import__('base64').b64encode(f"{env['NEXTCLOUD_USERNAME']}:{env['NEXTCLOUD_APP_PASSWORD']}".encode()).decode()
NS={'d':'DAV:','oc':'http://owncloud.org/ns'}

def req(method,url,body=None,headers=None):
    h={'Authorization':AUTH,'User-Agent':'nc-dedupe/1.0'}
    if headers: h.update(headers)
    r=urllib.request.Request(url,data=body,method=method,headers=h)
    try:
        with urllib.request.urlopen(r,timeout=60) as resp:
            return resp.status,resp.read()
    except urllib.error.HTTPError as e:
        return e.code,e.read()

PROPFIND='''<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><d:getcontentlength/><d:resourcetype/><oc:checksums/></d:prop></d:propfind>'''.encode()

def listdir(path):
    """return {name:(size,sha1|None,is_dir)}"""
    url=BASE+'/'+urllib.parse.quote(path.strip('/'))
    st,body=req('PROPFIND',url,PROPFIND,{'Depth':'1','Content-Type':'application/xml'})
    if st not in (207,200):
        print(f"LIST FAIL {path}: {st} {body[:200]}"); return None
    root=ET.fromstring(body)
    out={}
    for resp in root.findall('d:response',NS):
        href=resp.find('d:href',NS).text
        name=urllib.parse.unquote(href.rstrip('/').split('/')[-1])
        if name==path.strip('/').split('/')[-1] or not name: continue
        is_dir=resp.find('.//d:collection',NS) is not None
        size_el=resp.find('.//d:getcontentlength',NS)
        size=int(size_el.text) if size_el is not None and size_el.text else 0
        ck_el=resp.find('.//oc:checksums',NS)
        sha=None
        if ck_el is not None and ck_el.text:
            m=re.search(r'SHA1:([0-9a-fA-F]+)',ck_el.text)
            if m: sha=m.group(1).lower()
        out[name]=(size,sha,is_dir)
    return out

CANON='InstantUpload/Camera'
DUPS=['InstantUpload/null/DCIM/Camera',
      'PhoneBackup/DCIM/Camera']
# nested self-referential path discovered earlier
DUPS.append('InstantUpload/null/Android/media/com.nextcloud.client/nextcloud/tbaltzakis@cloudless.gr@cloud.cloudless.gr/InstantUpload/Camera')
ARCH='Conflicts-Archive/Dedupe-20261111'

def enc(p): return '/'.join(urllib.parse.quote(seg) for seg in p.split('/'))

canon=listdir(CANON)
if canon is None: sys.exit(1)
print(f"canonical {CANON}: {len(canon)} entries")
canon_files={k:v for k,v in canon.items() if not v[2]}

stats={'deleted':0,'moved':0,'kept_no_match':0,'errors':0}
for dup in DUPS:
    files=listdir(dup)
    if files is None:
        print(f"(skip missing {dup})"); continue
    files={k:v for k,v in files.items() if not v[2]}
    print(f"dup {dup}: {len(files)} files")
    for name,(size,sha,is_dir) in files.items():
        c=canon_files.get(name)
        identical=False
        if c and c[0]==size:
            if sha and c[1] and sha==c[1]:
                identical=True
            elif not sha or not c[1]:
                identical='unknown'  # can't verify -> archive
        if identical is True:
            st,_=req('DELETE',BASE+'/'+enc(f'{dup}/{name}'))
            if st in (200,204): stats['deleted']+=1
            else: stats['errors']+=1; print(f"  DEL FAIL {name}: {st}")
        else:
            # move to archive preserving relative path
            dest=f"{ARCH}/{dup}/{name}"
            # ensure parent collection exists (MKCOL)
            parts=dest.split('/')[:-1]
            for i in range(1,len(parts)+1):
                p='/'.join(parts[:i])
                req('MKCOL',BASE+'/'+enc(p))
            st,_=req('MOVE',BASE+'/'+enc(f'{dup}/{name}'),headers={'Destination':BASE+'/'+enc(dest),'Overwrite':'F'})
            if st in (201,204): stats['moved']+=1
            else: stats['errors']+=1; print(f"  MOVE FAIL {name}: {st}")
        if (stats['deleted']+stats['moved'])%50==0:
            print(f"  progress: {stats}")
    time.sleep(0.2)
print("DONE",stats)

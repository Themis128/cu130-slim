#!/usr/bin/env python3
"""n8n 2.x credential & API-key ops — mint public API keys, decrypt/re-encrypt
stored credentials, inspect deployment keys.

Why: n8n has no CLI/UI headless path for API-key creation, and key rotation
(N8N_ENCRYPTION_KEY changed, deployment_key regenerated) silently orphans
stored credentials — workflows then fail with decrypt errors on next run.

Secrets are read inside the n8n container env; nothing is printed.

Usage (from repo root):
  python -X utf8 .devin/skills/n8n-cloudless/scripts/n8n-cred-ops.py <cmd>

Commands:
  key-mint <label>        Mint a public API key (JWT) into user_api_keys;
                        prints the JWT (write to .env N8N_API_KEY).
  cred-check              Decrypt-verify every credentials_entity row;
                        reports name → OK/FAIL (+ field lengths, no values).
  cred-encrypt <name> <json-file>
                        Re-encrypt a credential row's data with the current
                        N8N_ENCRYPTION_KEY (OpenSSL Salted__/AES-256-CBC).
  keys-list               Show deployment_key rows (type/algorithm/status).

Env: runs against the compose stack (containers `n8n`, `social-postgres`,
db `n8n`, user `social_user`). Requires N8N_ENCRYPTION_KEY from .env.
"""
import json
import os
import subprocess
import sys
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
N8N_DB = ["docker", "exec", "social-postgres", "psql", "-U", "social_user", "-d", "n8n", "-t", "-A", "-c"]
N8N_DB_STDIN = ["docker", "exec", "-i", "social-postgres", "psql", "-U", "social_user", "-d", "n8n"]

def env_key():
    for line in open(os.path.join(ROOT, ".env"), encoding="utf8"):
        if line.startswith("N8N_ENCRYPTION_KEY="):
            return line.split("=", 1)[1].strip()

def psql(sql):
    r = subprocess.run(N8N_DB + [sql], capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"psql failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()

def psql_stdin(sql):
    r = subprocess.run(N8N_DB_STDIN, input=sql, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"psql failed: {r.stderr.strip()[:200]}")
    return r.stdout.strip()

NODE_DEC = r'''
const crypto=require('crypto');
const blob=Buffer.from(process.env.BLOB,'base64');
const salt=blob.subarray(8,16),ct=blob.subarray(16);
const evp=(p,s,l)=>{let d=Buffer.alloc(0),prev='';while(d.length<l){prev=crypto.createHash('md5').update(Buffer.concat([Buffer.from(prev),Buffer.from(p),s])).digest();d=Buffer.concat([d,prev]);}return d.subarray(0,l)};
const ki=evp(process.env.N8N_ENCRYPTION_KEY,salt,48);
try{
  const d=crypto.createDecipheriv('aes-256-cbc',ki.subarray(0,32),ki.subarray(32,48));
  const j=JSON.parse(Buffer.concat([d.update(ct),d.final()]).toString('utf8'));
  console.log('OK',JSON.stringify(Object.fromEntries(Object.entries(j).map(([k,v])=>[k,String(v??'').length]))));
}catch(e){console.log('FAIL',e.message.slice(0,60));}
'''

NODE_ENC = r'''
const crypto=require('crypto');
const obj=JSON.parse(require('fs').readFileSync('/tmp/cred-in.json','utf8'));
const salt=crypto.randomBytes(8);
const evp=(p,s,l)=>{let d=Buffer.alloc(0),prev='';while(d.length<l){prev=crypto.createHash('md5').update(Buffer.concat([Buffer.from(prev),Buffer.from(p),s])).digest();d=Buffer.concat([d,prev]);}return d.subarray(0,l)};
const ki=evp(process.env.N8N_ENCRYPTION_KEY,salt,48);
const c=crypto.createCipheriv('aes-256-cbc',ki.subarray(0,32),ki.subarray(32,48));
const ct=Buffer.concat([c.update(JSON.stringify(obj),'utf8'),c.final()]);
console.log(Buffer.concat([Buffer.from('Salted__'),salt,ct]).toString('base64'));
'''

def node(script, env=None):
    e = {}
    for k, v in (env or {}).items():
        e["-e"] = None
    cmd = ["docker", "exec"]
    for k, v in (env or {}).items():
        cmd += ["-e", f"{k}={v}"]
    cmd += ["n8n", "node", "-e", script]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip()

def cmd_key_mint(label):
    """Mint a public API key: HS256 JWT, secret = sha256(even-indexed chars of
    N8N_ENCRYPTION_KEY), claims {sub:<owner uuid>, iss:'n8n', aud:'public-api'}.
    Row goes into user_api_keys.apiKey (raw token — the column stores the JWT,
    not a hash)."""
    uid = psql("SELECT id FROM \"user\" LIMIT 1;").splitlines()[0]
    script = r'''
const crypto=require('crypto');const enc=process.env.N8N_ENCRYPTION_KEY;
let b='';for(let i=0;i<enc.length;i+=2)b+=enc[i];
const sec=crypto.createHash('sha256').update(b).digest('hex');
const b64=o=>Buffer.from(JSON.stringify(o)).toString('base64url');
const pl={sub:process.env.UID,iss:'n8n',aud:'public-api',jti:crypto.randomUUID(),iat:Math.floor(Date.now()/1000)};
const h=b64({alg:'HS256',typ:'JWT'})+'.'+b64(pl);
console.log(h+'.'+crypto.createHmac('sha256',sec).update(h).digest('base64url'));
'''
    jwt = node(script, {"UID": uid})
    assert jwt.count(".") == 2, f"mint failed: {jwt[:80]}"
    scopes = json.dumps(["workflow:read", "workflow:list", "workflow:create", "workflow:update",
                         "workflow:delete", "workflow:activate", "workflow:deactivate",
                         "execution:read", "execution:list", "execution:delete", "execution:retry",
                         "credential:read", "credential:create", "credential:update",
                         "tag:read", "tag:list", "user:read", "user:list"])
    psql_stdin(
        "INSERT INTO user_api_keys (id,\"userId\",label,\"apiKey\",scopes,audience) "
        f"VALUES ('{uuid.uuid4()}','{uid}','{label}','{jwt}','{scopes}'::json,'public-api');"
    )
    print(jwt)

def cmd_cred_check():
    rows = psql("SELECT name,data FROM credentials_entity ORDER BY name;").splitlines()
    for row in rows:
        name, blob = row.split("|", 1)
        out = node(NODE_DEC, {"BLOB": blob})
        print(f"{name}: {out}")

def cmd_cred_encrypt(name, json_file):
    jf = os.path.abspath(json_file)
    subprocess.run(["docker", "cp", jf, "n8n:/tmp/cred-in.json"], check=True)
    blob = node(NODE_ENC)
    assert blob.startswith("U2FsdGVk"), f"encrypt failed: {blob[:80]}"
    safe = name.replace("'", "''")
    print(psql_stdin(f"UPDATE credentials_entity SET data='{blob}' WHERE name='{safe}';"))

def cmd_keys_list():
    print(psql("SELECT type,algorithm,status,\"updatedAt\"::date FROM deployment_key ORDER BY type;"))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "keys-list"
    if cmd == "key-mint":
        cmd_key_mint(sys.argv[2] if len(sys.argv) > 2 else "ops-key")
    elif cmd == "cred-check":
        cmd_cred_check()
    elif cmd == "cred-encrypt":
        cmd_cred_encrypt(sys.argv[2], sys.argv[3])
    elif cmd == "keys-list":
        cmd_keys_list()
    else:
        sys.exit(__doc__)

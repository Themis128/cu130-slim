#!/usr/bin/env python3
"""Drive the dockerized Playwright MCP server over stdio to log in to X.

Launches mcr.microsoft.com/playwright/mcp in a container, performs the
two-step X onboarding login (username step, then password step), verifies
the authenticated state, and exports the session storage state to
.playwright-data/x_storage_state.json for transplant into browser-novnc.

Credentials are read from social-automation/backend/.env and never printed.
"""
import json
import os
import re
import subprocess
import sys
import time

REPO = "/home/tbaltzakis/cu130-slim"
IMAGE = "mcr.microsoft.com/playwright/mcp:latest"
DOCKER_ARGS = [
    "docker", "run", "-i", "--rm", "--init",
    "--network", "host", "--shm-size=2g",
    "-v", f"{REPO}:/workspace",
    "-v", f"{REPO}/.playwright-data:/home/pwuser/.playwright-data",
    "-w", "/workspace",
    IMAGE,
    "--config", "/workspace/.playwright-data/pw-mcp-config.json",
    "--user-data-dir", "/home/pwuser/.playwright-data/profile",
    "--ignore-https-errors",
    "--timeout-action", "20000",
    "--timeout-navigation", "120000",
]


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


class McpClient:
    def __init__(self):
        self.proc = subprocess.Popen(
            DOCKER_ARGS, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1,
        )
        self._id = 0

    def _send(self, msg):
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()

    def _read(self):
        line = self.proc.stdout.readline()
        if not line:
            raise RuntimeError("MCP server closed stdout")
        return json.loads(line)

    def request(self, method, params=None, timeout=180):
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                    "params": params or {}})
        deadline = time.time() + timeout
        while time.time() < deadline:
            resp = self._read()
            if resp.get("id") == rid:
                return resp
        raise TimeoutError(f"no response for {method}")

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def call(self, tool, arguments, timeout=180):
        resp = self.request("tools/call", {
            "name": tool, "arguments": arguments}, timeout=timeout)
        result = resp.get("result") or {}
        parts = result.get("content") or []
        text = "\n".join(p.get("text", "") for p in parts if p.get("type") == "text")
        if result.get("isError"):
            raise RuntimeError(f"{tool} failed: {text[:800]}")
        return text

    def close(self):
        try:
            self.proc.terminate()
            self.proc.wait(timeout=10)
<<<<<<< HEAD
        except (OSError, subprocess.SubprocessError):
            self.proc.kill()


=======
        except Exception:
            self.proc.kill()


def snapshot_text(client):
    return client.call("browser_snapshot", {})


def find_submit_button(snap, names):
    """Find the <button> wrapping an exact-named child, else a named button."""
    lines = snap.splitlines()
    direct = None
    for i, line in enumerate(lines):
        if "ref=" not in line:
            continue
        ref = line.split("ref=", 1)[1].split("]")[0].split()[0].rstrip("]")
        low = line.strip().lower()
        for n in names:
            if f'button "{n}"' in low:
                return ref
            if low.endswith(f": {n.lower()}"):
                # walk back to the enclosing button (less-indented line)
                indent = len(line) - len(line.lstrip())
                for j in range(i - 1, -1, -1):
                    pl = lines[j]
                    pind = len(pl) - len(pl.lstrip())
                    if pind < indent and "button" in pl and "ref=" in pl:
                        return pl.split("ref=", 1)[1].split("]")[0].split()[0].rstrip("]")
                direct = direct or ref
    return direct


def click_visible_button(client, names):
    """Real mouse click on the *visible* button whose label matches.

    X renders duplicate hidden buttons in localized clones — offsetParent
    filtering picks the live one, and page.mouse.click is trusted input
    (synthetic el.click()/Enter are ignored by the onboarding funnel).
    """
    code = """async (page) => {
        const names = %s;
        const target = await page.evaluate((names) => {
            const els = [...document.querySelectorAll('div[role=button],button,[role=button]')];
            const vis = els.filter(e => {
                const r = e.getBoundingClientRect();
                return r.width > 0 && r.height > 0 && e.offsetParent !== null;
            });
            const exact = vis.find(e => names.some(n => (e.innerText || '').trim() === n));
            const el = exact || vis.find(e => names.some(n => (e.innerText || '').includes(n)));
            if (!el) return null;
            const r = el.getBoundingClientRect();
            return { x: r.x + r.width / 2, y: r.y + r.height / 2, label: (el.innerText || '').trim().slice(0, 60) };
        }, names);
        if (!target) return 'NOT_FOUND';
        await page.mouse.click(target.x, target.y);
        return 'CLICKED: ' + target.label;
    }""" % json.dumps(names)
    out = client.call("browser_run_code_unsafe", {"code": code})
    print("  click:", out.strip().splitlines()[-1][:120] if out.strip() else out)
    return "CLICKED" in out


def find_ref(snap, *, role=None, name_substrs=None, exact_names=None):
    """Find first matching ref in a playwright-mcp YAML-ish snapshot."""
    for line in snap.splitlines():
        if "ref=" not in line:
            continue
        ref = line.split("ref=", 1)[1].split("]")[0].split()[0].rstrip("]")
        low = line.lower()
        if role and f'"{role}"' not in low and f" {role} " not in low:
            continue
        if exact_names and not any(
            f'"{n}"' in line or line.rstrip().endswith(f": {n}")
            for n in exact_names
        ):
            continue
        if name_substrs and not any(s in low for s in name_substrs):
            continue
        return ref
    return None


>>>>>>> aa5f26a6 (fix(analytics): exclude archived posts, drop unsupported IG metrics, fix LinkedIn benchmark label)
def main():
    env = load_env(f"{REPO}/.env")
    # X reported "no active account" for the gmail — the @handle is the
    # reliable identifier; fall back to env vars.
    ident = (sys.argv[1] if len(sys.argv) > 1 else "").lstrip("@") or \
        (env.get("TWITTER_LOGIN_USERNAME") or env.get("TWITTER_LOGIN_EMAIL") or "").strip()
    password = (env.get("TWITTER_LOGIN_PASSWORD") or "").strip()
    if not ident or not password:
        print("ERROR: TWITTER_LOGIN_* missing in backend .env")
        sys.exit(1)
    print(f"identifier loaded (len {len(ident)}), password loaded (len {len(password)})")

    client = McpClient()
    try:
        client.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "x-login-driver", "version": "1.0"},
        })
        client.notify("notifications/initialized")
        tools = client.request("tools/list")
        names = [t["name"] for t in tools["result"]["tools"]]
        print("tools:", ", ".join(names))

        # Step 0: already logged in?
        print(client.call("browser_navigate", {"url": "https://x.com/home"})[-400:])
        time.sleep(3)
        state = client.call("browser_evaluate", {"function": """() => ({
            url: location.href,
            loggedIn: !!document.querySelector('[data-testid=SideNav_AccountSwitcher_Button]')
        })"""})
        print("state:", state[-300:])
        if '"loggedIn": true' in state or "loggedIn\":true" in state.replace(" ", ""):
            print("ALREADY LOGGED IN")
            export(client)
            return

        # Pull the @handle once for a possible unusual-activity verify step.
        handle = subprocess.run(
            ["docker", "exec", "-i", "social-postgres", "sh", "-c",
<<<<<<< HEAD
             ('psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
              "\"SELECT username FROM social_accounts WHERE platform='twitter' LIMIT 1\"")],
            capture_output=True, text=True, check=False).stdout.strip() or ident
=======
             'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc '
             "\"SELECT username FROM social_accounts WHERE platform='twitter' LIMIT 1\""],
            capture_output=True, text=True).stdout.strip() or ident
>>>>>>> aa5f26a6 (fix(analytics): exclude archived posts, drop unsupported IG metrics, fix LinkedIn benchmark label)

        # Run the whole onboarding funnel in one shot with Playwright waits —
        # snapshot round-trips keep catching the Loading dialog and the
        # background page's duplicate DOM.
        login_code = """async (page) => {
<<<<<<< HEAD
            const IDENT = __IDENT__, PASS = __PASS__, HANDLE = __HANDLE__;
=======
            const IDENT = %s, PASS = %s, HANDLE = %s;
>>>>>>> aa5f26a6 (fix(analytics): exclude archived posts, drop unsupported IG metrics, fix LinkedIn benchmark label)
            const log = [];
            const clickNamed = async (names) => {
                const t = await page.evaluate((names) => {
                    const els = [...document.querySelectorAll('div[role=button],button,[role=button]')]
                        .filter(e => e.offsetParent !== null);
                    const el = els.find(e => names.includes((e.innerText || '').trim()));
                    if (!el) return null;
                    el.scrollIntoView({ block: 'center' });
                    const r = el.getBoundingClientRect();
                    return { x: r.x + r.width / 2, y: r.y + r.height / 2, label: (el.innerText || '').trim() };
                }, names);
                if (!t) return false;
                await page.mouse.click(t.x, t.y);
                log.push('clicked:' + t.label.slice(0, 40));
                return true;
            };

            await page.goto('https://x.com/i/flow/login', { waitUntil: 'domcontentloaded' });
            const shot = (n) => page.screenshot({ path: '/workspace/.playwright-mcp/xlogin-' + n + '.png' });
            const waitIdle = async () => {
                await page.locator('progressbar').first().waitFor({ state: 'hidden', timeout: 30000 }).catch(() => {});
                await page.locator('[data-testid=mask]').waitFor({ state: 'hidden', timeout: 30000 }).catch(() => {});
            };
            await waitIdle();
            const clickRole = async (name) => {
                const b = page.getByRole('button', { name, exact: true }).locator('visible=true').first();
                if (!(await b.count())) { log.push('btn-missing:' + name); return false; }
                try {
                    await b.waitFor({ state: 'visible', timeout: 15000 });
                    await b.click({ timeout: 10000 });
                    log.push('clicked:' + name);
                    return true;
                } catch (e) { log.push('btn-fail:' + name + ':' + String(e).slice(0, 80)); return false; }
            };

            const idIn = page.locator('input[name="username_or_email"]:visible').first();
            await idIn.waitFor({ timeout: 40000 });
            await idIn.fill(IDENT);
            log.push('identifier filled');
            await shot('1-ident');
            // Continue enables only after the field registers input.
            await clickRole('Continue') || await clickRole('Next') || await clickRole('Συνέχεια');
            await waitIdle();

            const pass = page.locator('input[name="password"]:visible').first();
            for (let i = 0; i < 5; i++) {
                try { await pass.waitFor({ timeout: 20000 }); break; } catch {}
                await shot('3-wait-' + i);
                if (await clickRole('Use password')) { await waitIdle(); continue; }
                const v = await page.evaluate(() => {
                    const el = [...document.querySelectorAll('input')]
                        .find(e => e.offsetParent && !e.disabled && e.type !== 'hidden' && e.type !== 'password');
                    return el ? (el.name || null) : null;
                });
                log.push('iter' + i + ' verify-input=' + v);
                if (v) {
                    const vin = page.locator('input[name="' + v + '"]:visible').first();
                    if (await vin.count() && await vin.isEditable()) {
                        await vin.fill(HANDLE);
                        log.push('verify-handle filled via ' + v);
                        await clickRole('Next') || await clickRole('Continue') || await clickRole('Συνέχεια');
                        await waitIdle();
                    }
                }
            }
            await shot('4-before-pass');
            if (!(await pass.count())) { log.push('NO_PASSWORD_FIELD url=' + page.url()); return log.join('\\n'); }
            await pass.fill(PASS);
            log.push('password filled');
            await clickRole('Log in') || await clickRole('Continue') || await clickRole('Σύνδεση') || await clickRole('Συνέχεια');
            await shot('5-after-login');
            await page.waitForURL(u => !u.pathname.includes('/i/flow') && !u.pathname.includes('/i/jf'), { timeout: 60000 }).catch(() => {});
            await waitIdle();
            log.push('url=' + page.url());
            log.push('switcher=' + !!(await page.locator('[data-testid=SideNav_AccountSwitcher_Button]').count()));
            log.push('arkose=' + !!(await page.locator('iframe[src*=arkose]').count()));
            log.push('body=' + (await page.locator('body').innerText()).replace(/\\s+/g, ' ').slice(0, 200));
            return log.join('\\n');
<<<<<<< HEAD
        }""".replace("__IDENT__", json.dumps(ident)) \
             .replace("__PASS__", json.dumps(password)) \
             .replace("__HANDLE__", json.dumps(handle))
        out = client.call("browser_run_code_unsafe", {"code": login_code}, timeout=240)
        # The tool echoes the code; the Result section holds the log text.
        m = re.search(r'### Result\n(.*?)(?:\n### |\Z)', out, re.DOTALL)
=======
        }""" % (json.dumps(ident), json.dumps(password), json.dumps(handle))
        out = client.call("browser_run_code_unsafe", {"code": login_code}, timeout=240)
        # The tool echoes the code; the Result section holds the log text.
        m = re.search(r'### Result\n(.*?)(?:\n### |\Z)', out, re.S)
>>>>>>> aa5f26a6 (fix(analytics): exclude archived posts, drop unsupported IG metrics, fix LinkedIn benchmark label)
        flow_text = (m.group(1) if m else out[-800:])
        print("flow:", flow_text[:1200])
        if "couldn't find an active X account" in flow_text:
            print("SOFTBLOCK: X funnel rejects the identifier although the "
                  "account exists (OAuth verified) — anti-automation wall, "
                  "not bad credentials. Manual noVNC login required.")
            sys.exit(4)
        time.sleep(3)

        # Verify — parse the RESULT object only (the echoed JS source also
        # contains the literal strings we check for → false positives).
        for _ in range(10):
            state = client.call("browser_evaluate", {"function": """() => ({
                url: location.href,
                loggedIn: !!document.querySelector('[data-testid=SideNav_AccountSwitcher_Button]'),
                captcha: !!document.querySelector('iframe[src*=arkose]'),
                err: (document.body.innerText.match(/password you entered is incorrect/i)||[])[0] || null
            })"""})
<<<<<<< HEAD
            res = re.search(r'\{[^{}]*"loggedIn"[^{}]*\}', state, re.DOTALL)
=======
            res = re.search(r'\{[^{}]*"loggedIn"[^{}]*\}', state, re.S)
>>>>>>> aa5f26a6 (fix(analytics): exclude archived posts, drop unsupported IG metrics, fix LinkedIn benchmark label)
            res = res.group(0) if res else ""
            print("state:", res[:300] or state[-200:])
            compact = res.replace(" ", "")
            if '"captcha":true' in compact:
                print("CAPTCHA — manual noVNC needed"); sys.exit(2)
            if '"err":"' in res and "incorrect" in res:
                print("BAD PASSWORD"); sys.exit(3)
            if '"loggedIn":true' in compact:
                break
            time.sleep(3)
        export(client)
        print("LOGIN OK")
    finally:
        client.close()


def export(client):
    code = """async (page) => {
        const cookies = (await page.context().cookies())
            .filter(c => c.domain.includes('x.com') || c.domain.includes('twitter.com'));
        return JSON.stringify(cookies);
    }"""
    out = client.call("browser_run_code_unsafe", {"code": code})
<<<<<<< HEAD
    m = re.search(r'### Result\n(.*?)(?:\n### |\Z)', out, re.DOTALL)
=======
    m = re.search(r'### Result\n(.*?)(?:\n### |\Z)', out, re.S)
>>>>>>> aa5f26a6 (fix(analytics): exclude archived posts, drop unsupported IG metrics, fix LinkedIn benchmark label)
    raw = (m.group(1).strip() if m else out).strip()
    # Result is a JSON-encoded string of the JSON array — decode twice.
    try:
        cookies = json.loads(json.loads(raw.strip('"')))
<<<<<<< HEAD
    except (json.JSONDecodeError, TypeError, ValueError):
        try:
            cookies = json.loads(raw)
        except (json.JSONDecodeError, TypeError, ValueError):
=======
    except Exception:
        try:
            cookies = json.loads(raw)
        except Exception:
>>>>>>> aa5f26a6 (fix(analytics): exclude archived posts, drop unsupported IG metrics, fix LinkedIn benchmark label)
            cookies = []
    path = f"{REPO}/.playwright-data/x_cookies.json"
    with open(path, "w") as f:
        json.dump(cookies, f, indent=1)
    os.chmod(path, 0o600)
    names = [c.get("name") for c in cookies if isinstance(c, dict)]
    print(f"export: wrote {len(cookies)} cookies -> {path} :: {','.join(names)}")


if __name__ == "__main__":
    main()

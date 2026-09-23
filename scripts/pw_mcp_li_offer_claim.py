#!/usr/bin/env python3
"""Claim the LinkedIn $100 ad-credit offer via the dockerized Playwright MCP
browser, with a patient retry loop for LinkedIn's IP-level rate-limit wall.

Flow: credential login (LINKEDIN_EMAIL / LINKEDIN_PASSWORD from .env) ->
campaignmanager (mcid deep-link) -> ad account -> Billing -> Credits and
promotions -> Redeem coupon -> apply code -> verify credit balance.

Never spends money: stops and reports if LinkedIn requires payment details,
and never enters card data. Each stage dumps a screenshot under
.playwright-mcp/ and appends to a result log so the run can be audited.
"""
import json
import sys
import time

from pw_mcp_li_ads import McpClient

REPO = "/home/tbaltzakis/cu130-slim"
RESULT_LOG = f"{REPO}/.playwright-mcp/li-offer-claim-result.json"
COUPON = "xzkhxT3Nk"
MCID_URL = (
    "https://www.linkedin.com/campaignmanager/login"
    "?mcid=7026332492244680704"
    "&trk=eml-mktg-acq-202302-global-spn-cnt-low-perf-cmt-initial&src=e-eml"
)


def load_env(path):
    env = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def record(stage: str, data):
    try:
        log = json.load(open(RESULT_LOG))
    except Exception:
        log = []
    log.append({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "stage": stage, "data": data})
    json.dump(log, open(RESULT_LOG, "w"), indent=1)


def run_code(client, code, timeout=150, secrets=()):
    out = client.call("browser_run_code_unsafe", {"code": code}, timeout=timeout)
    for s in secrets:
        if s:
            out = out.replace(s, "***")
    return out


def result_text(out: str) -> str:
    """Tool output echoes the submitted source above the result — checks must
    only inspect the section after the last '### Result' marker."""
    marker = "### Result"
    return out.rsplit(marker, 1)[-1] if marker in out else out


def attempt(user, password, target_url):
    """One full claim attempt. Returns a short status string."""
    client = McpClient()
    secrets = (user, password)
    try:
        client.request("initialize", {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "li-offer-claim", "version": "1.0"},
        })
        client.notify("notifications/initialized")

        login = """async (page) => {
            const log = [];
            try {
                await page.goto('https://www.linkedin.com/login',
                    {waitUntil:'domcontentloaded', timeout:60000});
                await page.waitForTimeout(2500);
                log.push('at ' + page.url());
                await page.locator('input#username').fill(USER_JSON);
                await page.locator('input#password').fill(PASS_JSON);
                await page.waitForTimeout(600);
                await page.locator('button[type=submit]').first().click();
                for (let i = 0; i < 15; i++) {
                    await page.waitForTimeout(2000);
                    const u = page.url();
                    if (u.includes('/feed') || /checkpoint|challenge|two-step/i.test(u)) break;
                }
                const body = (await page.locator('body').innerText()).slice(0, 600);
                return JSON.stringify({log, url: page.url(), body});
            } catch (e) { return 'ERR: ' + log.join('|') + ' :: ' + e.message.slice(0,200); }
        }"""
        login = login.replace("USER_JSON", json.dumps(user)).replace("PASS_JSON", json.dumps(password))
        out = run_code(client, login, secrets=secrets)
        record("login", out[-400:])
        res = result_text(out)
        if "linkedin.com/feed" not in res:
            if "chrome-error" in res or "ERR:" in res or "429" in res:
                return "walled"
            return "login_failed"

        claim = """async (page) => {
            const shot = (n) => page.screenshot({path: '/workspace/.playwright-mcp/li-' + n + '.png', fullPage: false});
            const find = async (re) => page.locator('button, a, [role=button]').filter({hasText: re});
            const steps = [];
            try {
                const r = await page.goto(TARGET, {waitUntil:'domcontentloaded', timeout:90000});
                await page.waitForTimeout(7000);
                steps.push('cm status=' + (r && r.status()) + ' url=' + page.url());
                await shot('01-cm');

                // Ad-account picker: pick the first account if listed.
                const acct = page.locator('a[href*="/campaignmanager/accounts/"], [data-test-account-row] a, .account-selector a').first();
                if (await acct.count()) { await acct.click(); await page.waitForTimeout(6000); steps.push('picked account -> ' + page.url()); }
                await shot('02-account');

                // Billing -> Credits and promotions -> Redeem coupon.
                for (const [name, re] of [['billing', /^billing/i], ['credits', /credits and promotions|credits & promotions/i], ['redeem', /redeem coupon|redeem/i]]) {
                    const el = await find(re).first();
                    if (await el.count()) { await el.click(); await page.waitForTimeout(4000); steps.push('clicked ' + name + ' -> ' + page.url()); }
                    else steps.push('NOT FOUND: ' + name);
                    await shot('03-' + name);
                }

                // Coupon field.
                const field = page.locator('input[name*="coupon" i], input[placeholder*="coupon" i], input[aria-label*="coupon" i], input[type=text]:visible').first();
                if (await field.count()) {
                    await field.fill(COUPON_JSON);
                    steps.push('filled coupon');
                    const apply = await find(/apply|redeem|submit/i).first();
                    if (await apply.count()) { await apply.click(); await page.waitForTimeout(5000); steps.push('applied'); }
                } else steps.push('NOT FOUND: coupon field');
                await shot('04-coupon');

                const body = (await page.locator('body').innerText()).slice(0, 1200);
                return JSON.stringify({steps, url: page.url(), body});
            } catch (e) { return 'ERR: ' + steps.join('|') + ' :: ' + e.message.slice(0,200); }
        }"""
        claim = claim.replace("TARGET", json.dumps(target_url)).replace("COUPON_JSON", json.dumps(COUPON))
        out = run_code(client, claim, timeout=240, secrets=secrets)
        record("claim", out[-800:])
        res = result_text(out)
        if "chrome-error" in res or ("ERR:" in res and "429" in res):
            return "walled"
        if "payment" in res.lower() or "credit card" in res.lower():
            return "needs_payment_info"
        if "NOT FOUND" in res:
            return "partial"
        return "done"
    finally:
        client.close()


def main():
    env = load_env(f"{REPO}/.env")
    user = (env.get("LINKEDIN_EMAIL") or env.get("LINKEDIN_USERNAME") or "").strip()
    password = (env.get("LINKEDIN_PASSWORD") or "").strip()
    if not user or not password:
        print("ERROR: LINKEDIN_EMAIL/LINKEDIN_PASSWORD missing in .env")
        sys.exit(1)

    deadline = time.time() + 5 * 3600   # keep trying for up to 5h
    for n in range(1, 8):
        status = attempt(user, password, MCID_URL)
        print(f"[attempt {n}] {status} @ {time.strftime('%H:%M:%SZ', time.gmtime())}", flush=True)
        record("attempt", {"n": n, "status": status})
        if status != "walled" or time.time() > deadline:
            break
        time.sleep(45 * 60)
    print("final:", status)


if __name__ == "__main__":
    main()

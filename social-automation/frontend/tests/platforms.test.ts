import { test, expect, API_BASE, type AuthTokens } from './helpers/auth';
import { randomUUID } from 'crypto';

/**
 * Per-platform coverage — every social/messaging platform the stack
 * supports. Two layers, all against the REAL backend (no mocks):
 *
 *  1. API contract — platform routers must answer with a handled status
 *     (<500). A bogus account UUID must produce 4xx, never a 500 — a 500
 *     here is a real backend bug (unhandled DoesNotExist / validation).
 *  2. UI surface — the /accounts connect cards and the platform dashboard
 *     pages (/tiktok, /telegram, /whatsapp, /messenger) render their
 *     platform-specific content.
 */

const BOGUS_ID = randomUUID();

interface PlatformProbe {
  id: string;
  name: string;
  /** GET path under /api/v1; {id} is replaced with a bogus UUID. null = no safe probe. */
  probe: string | null;
  /** Shown as an h3 card on /accounts. */
  card: boolean;
  /** Dedicated dashboard page route, if any. */
  page?: string;
  pageText?: string;
}

const PLATFORMS: PlatformProbe[] = [
  { id: 'linkedin',  name: 'LinkedIn',    probe: '/api/v1/linkedin/accounts/{id}/validate',        card: true },
  { id: 'twitter',   name: 'Twitter / X', probe: '/api/v1/twitter/{id}/dm/auto-reply',             card: true },
  { id: 'instagram', name: 'Instagram',   probe: '/api/v1/instagram/app-review/status',            card: true },
  { id: 'facebook',  name: 'Facebook',    probe: null,                                             card: true },
  { id: 'messenger', name: 'Messenger',   probe: '/api/v1/messenger/{id}/profile',                 card: true, page: '/messenger', pageText: 'Messenger' },
  { id: 'threads',   name: 'Threads',     probe: '/api/v1/threads/quota',                          card: true },
  { id: 'tiktok',    name: 'TikTok',      probe: '/api/v1/tiktok/accounts/{id}/health',            card: true, page: '/tiktok',    pageText: 'TikTok' },
  { id: 'telegram',  name: 'Telegram',    probe: '/api/v1/telegram/{id}/setup-status',             card: true, page: '/telegram',  pageText: 'Telegram' },
  { id: 'whatsapp',  name: 'WhatsApp',    probe: '/api/v1/whatsapp/{id}/setup-status',             card: false, page: '/whatsapp', pageText: 'WhatsApp' },
  { id: 'viber',     name: 'Viber',       probe: '/api/v1/viber/{id}/setup-status',                card: false },
  { id: 'bluesky',   name: 'Bluesky',     probe: '/api/v1/bluesky/{id}/status',                    card: false },
];

const KNOWN_PLATFORMS = new Set(PLATFORMS.map((p) => p.id));
const CARDED = PLATFORMS.filter((p) => p.card);
const PAGES = PLATFORMS.filter((p) => p.page);

function authHeaders(tokens: AuthTokens) {
  return { Authorization: `Bearer ${tokens.access_token}` };
}

test.describe('Platform API contract — real backend', () => {
  test('GET /accounts returns a list whose platforms are all known', async ({ request, authTokens }) => {
    const res = await request.get(`${API_BASE}/api/v1/accounts`, { headers: authHeaders(authTokens) });
    expect(res.status(), await res.text()).toBe(200);
    const body = await res.json();
    const accounts = Array.isArray(body) ? body : (body.accounts ?? body.items ?? []);
    expect(Array.isArray(accounts)).toBe(true);
    for (const acc of accounts) {
      expect(KNOWN_PLATFORMS.has(acc.platform), `unknown platform "${acc.platform}" on account ${acc.id}`).toBe(true);
    }
  });

  for (const p of PLATFORMS) {
    if (!p.probe) continue;
    test(`${p.name}: probe endpoint handles bogus account id without 5xx`, async ({ request, authTokens }) => {
      const url = p.probe!.replace('{id}', BOGUS_ID);
      const res = await request.get(`${API_BASE}${url}`, { headers: authHeaders(authTokens) });
      const body = await res.text();
      expect(res.status(), `${p.name} ${url} -> ${res.status()}: ${body.slice(0, 300)}`).toBeLessThan(500);
      // Handled errors must still be JSON, not an HTML error page.
      expect(() => JSON.parse(body), `${p.name} returned non-JSON`).not.toThrow();
    });
  }
});

test.describe('Platform pages — real backend', () => {
  for (const p of PAGES) {
    test(`${p.name} dashboard page renders platform content`, async ({ authenticatedPage: page }) => {
      await page.goto(p.page!);
      await expect(page.getByText(p.pageText!, { exact: false }).first()).toBeVisible({ timeout: 20000 });
    });
  }
});

test.describe('Accounts page — per-platform cards', () => {
  for (const p of CARDED) {
    test(`${p.name} card renders with connect affordance`, async ({ authenticatedPage: page }) => {
      await page.goto('/accounts');
      const card = page.locator('.bg-card, [class*="card"]').filter({ has: page.getByRole('heading', { name: p.name, level: 3 }) }).first();
      await expect(card).toBeVisible({ timeout: 20000 });
      // Every card exposes at least one actionable control (connect/setup/connections).
      await expect(
        card.getByRole('button').first(),
        `${p.name} card has no buttons`,
      ).toBeVisible();
    });
  }
});

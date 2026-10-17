// @vitest-environment node
/**
 * Next.js imports this module during server rendering, where there is no
 * window/localStorage. Token helpers must then fall back to the in-memory
 * values instead of crashing, and the request interceptor must still attach
 * the in-memory token.
 */
import { describe, it, expect } from 'vitest'
import type { AxiosAdapter } from 'axios'

const echoAuth: AxiosAdapter = async (config) => ({
  data: config.headers?.Authorization ?? null,
  status: 200,
  statusText: 'OK',
  headers: {},
  config,
})

describe('services/api without a browser', () => {
  it('imports with no window and keeps tokens in memory', async () => {
    expect(typeof (globalThis as { window?: unknown }).window).toBe('undefined')
    const mod = await import('./api')
    const api = mod.default

    expect(mod.getAccessToken()).toBeNull()
    expect(mod.getRefreshToken()).toBeNull()
    expect((await api.get('/ping', { adapter: echoAuth })).data).toBeNull()

    mod.setTokens('acc-1', 'ref-1')
    expect(mod.getAccessToken()).toBe('acc-1')
    expect(mod.getRefreshToken()).toBe('ref-1')
    expect((await api.get('/ping', { adapter: echoAuth })).data).toBe('Bearer acc-1')

    mod.logout() // clears tokens; must not try to navigate server-side
    expect(mod.getAccessToken()).toBeNull()
    expect(mod.getRefreshToken()).toBeNull()
    expect((await api.get('/ping', { adapter: echoAuth })).data).toBeNull()
  })
})

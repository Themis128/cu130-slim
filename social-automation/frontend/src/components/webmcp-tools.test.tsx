import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'
import { WebMcpTools } from './webmcp-tools'

describe('WebMcpTools', () => {
  it('registers WebMCP tools when document.modelContext is available', async () => {
    const registerTool = vi.fn().mockResolvedValue(undefined)
    ;(document as unknown as { modelContext: unknown }).modelContext = { registerTool }

    const { unmount } = render(<WebMcpTools />)

    await waitFor(() => expect(registerTool).toHaveBeenCalledTimes(2))
    const names = registerTool.mock.calls.map((c) => (c[0] as { name: string }).name)
    expect(names).toContain('list_platforms')
    expect(names).toContain('check_api_health')

    // each call passes an options bag carrying the AbortSignal
    for (const call of registerTool.mock.calls) {
      expect((call[1] as { signal: AbortSignal }).signal).toBeInstanceOf(AbortSignal)
    }

    unmount()
    delete (document as unknown as { modelContext?: unknown }).modelContext
  })

  it('list_platforms returns the supported platform set', async () => {
    let listPlatforms: (() => Promise<{ platforms: string[] }>) | undefined
    const registerTool = vi.fn().mockImplementation((tool: { name: string; execute: unknown }) => {
      if (tool.name === 'list_platforms') listPlatforms = tool.execute as typeof listPlatforms
      return Promise.resolve()
    })
    ;(document as unknown as { modelContext: unknown }).modelContext = { registerTool }

    render(<WebMcpTools />)
    await waitFor(() => expect(registerTool).toHaveBeenCalled())
    const res = await listPlatforms!()
    expect(res.platforms).toContain('linkedin')
    expect(res.platforms).toContain('tiktok')
    delete (document as unknown as { modelContext?: unknown }).modelContext
  })

  it('check_api_health queries /api/v1/health', async () => {
    let health: (() => Promise<{ status: number; ok: boolean }>) | undefined
    const registerTool = vi.fn().mockImplementation((tool: { name: string; execute: unknown }) => {
      if (tool.name === 'check_api_health') health = tool.execute as typeof health
      return Promise.resolve()
    })
    ;(document as unknown as { modelContext: unknown }).modelContext = { registerTool }
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ status: 200, ok: true }))

    render(<WebMcpTools />)
    await waitFor(() => expect(registerTool).toHaveBeenCalled())
    const res = await health!()
    expect(res).toEqual({ status: 200, ok: true })
    expect(fetch).toHaveBeenCalledWith('/api/v1/health')
    vi.unstubAllGlobals()
    delete (document as unknown as { modelContext?: unknown }).modelContext
  })

  it('renders nothing and does nothing without modelContext', () => {
    const { container, unmount } = render(<WebMcpTools />)
    expect(container.firstChild).toBeNull()
    unmount()
  })
})

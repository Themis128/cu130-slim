import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen } from '@testing-library/react'
import {
  isOrgAccount,
  preferredAccount,
  identityFromAccount,
  AccountAvatar,
  ObjectUrlImage,
} from './previewIdentity'
import type { SocialAccount } from '@/types'

const acc = (over: Partial<SocialAccount> = {}): SocialAccount =>
  ({ id: 'abcdef123456', platform: 'linkedin', status: 'active', ...over } as SocialAccount)

describe('isOrgAccount', () => {
  it('detects organization via account_type or meta_data', () => {
    expect(isOrgAccount(acc({ account_type: 'organization' }))).toBe(true)
    expect(isOrgAccount(acc({ meta_data: { account_type: 'organization' } } as never))).toBe(true)
    expect(isOrgAccount(acc({ account_type: 'personal' }))).toBe(false)
    expect(isOrgAccount(acc())).toBe(false)
  })
})

describe('preferredAccount', () => {
  it('ignores inactive and other-platform accounts', () => {
    expect(preferredAccount([acc({ platform: 'x' }), acc({ status: 'expired' })], 'linkedin')).toBeUndefined()
  })

  it('prefers an organization account on linkedin', () => {
    const personal = acc({ id: 'p', account_type: 'personal' })
    const org = acc({ id: 'o', account_type: 'organization' })
    expect(preferredAccount([personal, org], 'linkedin')?.id).toBe('o')
    expect(preferredAccount([personal], 'linkedin')?.id).toBe('p')
  })

  it('prefers a page/business account on facebook', () => {
    const personal = acc({ id: 'p', platform: 'facebook' })
    const page = acc({ id: 'pg', platform: 'facebook', account_type: 'page' })
    const biz = acc({ id: 'b', platform: 'facebook', is_business: true })
    expect(preferredAccount([personal, page], 'facebook')?.id).toBe('pg')
    expect(preferredAccount([personal, biz], 'facebook')?.id).toBe('b')
  })

  it('returns the first account for other platforms', () => {
    const a1 = acc({ id: '1', platform: 'tiktok' })
    const a2 = acc({ id: '2', platform: 'tiktok' })
    expect(preferredAccount([a1, a2], 'tiktok')?.id).toBe('1')
  })
})

describe('identityFromAccount', () => {
  it('builds identity from display_name + username', () => {
    const id = identityFromAccount(acc({ display_name: 'Themis', username: 'tbaltzakis' }))
    expect(id.name).toBe('Themis')
    expect(id.handle).toBe('tbaltzakis')
    expect(id.isOrg).toBe(false)
    expect(id.headline).toBeNull()
  })

  it('marks orgs as Company Page', () => {
    const id = identityFromAccount(acc({ account_type: 'organization' }))
    expect(id.isOrg).toBe(true)
    expect(id.headline).toBe('Company Page')
  })

  it('falls back to account-id-derived name when nothing else exists', () => {
    const id = identityFromAccount(acc({ display_name: '  ', username: '', account_id: '' }))
    expect(id.name).toBe('Account abcdef12')
    expect(id.handle).toBe('')
  })
})

describe('AccountAvatar', () => {
  it('renders the avatar image for safe URLs', () => {
    const id = identityFromAccount(acc({ display_name: 'Themis', username: 't', avatar_url: 'https://x/a.png' }))
    render(<AccountAvatar identity={id} />)
    expect(screen.getByAltText('Themis')).toHaveAttribute('src', 'https://x/a.png')
  })

  it('renders initials fallback otherwise', () => {
    const id = identityFromAccount(acc({ display_name: 'Themis Baltzakis', username: 't', avatar_url: null }))
    const { container } = render(<AccountAvatar identity={id} />)
    expect(container.textContent).toBe('TB')

    const single = identityFromAccount(acc({ display_name: 'Themis', avatar_url: 'javascript:x' }))
    const { container: c2 } = render(<AccountAvatar identity={single} />)
    expect(c2.textContent).toBe('T')
  })
})

describe('ObjectUrlImage', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('creates an object URL and renders it through SafeImage', () => {
    vi.stubGlobal('URL', Object.assign(URL, {
      createObjectURL: vi.fn(() => 'blob:http://localhost/f1'),
      revokeObjectURL: vi.fn(),
    }))
    const file = new File(['x'], 'f.png')
    render(<ObjectUrlImage file={file} alt="preview" />)
    expect(screen.getByAltText('preview')).toHaveAttribute('src', 'blob:http://localhost/f1')
  })
})

import { describe, it, expect, vi } from 'vitest'
import { render, waitFor } from '@testing-library/react'

const setCtx = vi.fn()

vi.mock('@/hooks/useAdvisor', () => ({
  useAdvisor: () => ({ setCtx }),
}))

vi.mock('@/hooks/useQueries', () => ({
  useAccounts: () => ({
    data: [
      {
        id: 'acc-msg-1',
        team_id: 'team-1',
        platform: 'messenger',
        account_id: 'msg-1',
        username: 'cloudless.gr',
        display_name: 'Cloudless Messenger',
        avatar_url: null,
        status: 'active',
        scopes: [],
        token_expires_at: null,
        last_sync_at: null,
        error_message: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        account_type: 'person',
        is_business: false,
        parent_account_id: null,
        meta_data: {},
      },
      {
        id: 'acc-li-1',
        team_id: 'team-1',
        platform: 'linkedin',
        account_id: 'li-1',
        username: 'cloudless.gr',
        display_name: 'Cloudless',
        avatar_url: null,
        status: 'active',
        scopes: [],
        token_expires_at: null,
        last_sync_at: null,
        error_message: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        account_type: 'organization',
        is_business: true,
        parent_account_id: null,
        meta_data: {},
      },
    ],
  }),
  useCreatePost: () => ({ mutateAsync: vi.fn() }),
  useUploadMedia: () => ({ mutateAsync: vi.fn() }),
  useGenerateContent: () => ({ mutateAsync: vi.fn() }),
  usePillars: () => ({ data: [] }),
  useBriefs: () => ({ data: [] }),
  useContentTemplates: () => ({ data: [] }),
}))

vi.mock('@/services/api', () => ({
  contentApi: { publishNow: vi.fn() },
  aiApi: { generateContent: vi.fn() },
  brandApi: { scoreCompliance: vi.fn() },
  mediaUrl: (path: string) => path,
}))

vi.mock('@/components/content/VoiceRecorder', () => ({
  VoiceRecorder: () => null,
}))

vi.mock('@/components/content/SpellCheckButton', () => ({
  SpellCheckButton: () => null,
}))

vi.mock('@/components/content/SeoPanel', () => ({
  SeoPanel: () => null,
}))

vi.mock('@/components/ui/MediaPickerDialog', () => ({
  MediaPickerDialog: () => null,
}))

vi.mock('@/components/ui/MusicPickerDialog', () => ({
  MusicPickerDialog: () => null,
}))

vi.mock('@/components/content/previewIdentity', () => ({
  preferredAccount: (accounts: any[], platform: string) => accounts.find((a) => a.platform === platform) ?? null,
  identityFromAccount: (account: any) => ({
    name: account.display_name ?? account.username ?? 'Account',
    handle: account.username ?? 'handle',
    headline: null,
  }),
  ObjectUrlImage: () => null,
  AccountAvatar: () => null,
  isOrgAccount: (a: any) => a.account_type === 'organization',
}))

// Import after mocks
import NewPostPage from '../../app/(dashboard)/content/new/page'

describe('NewPostPage platform guards', () => {
  it('does not crash and prunes unsupported platform ids (e.g. messenger)', async () => {
    render(<NewPostPage />)

    await waitFor(() => {
      expect(setCtx).toHaveBeenCalled()
    })

    const last = setCtx.mock.calls.at(-1)?.[0]
    expect(last?.selectedPlatforms).toContain('linkedin')
    expect(last?.selectedPlatforms).not.toContain('messenger')
  })
})


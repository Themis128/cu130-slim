'use client'

import { useEffect, useMemo } from 'react'
import { cn, isSafeImageUrl } from '@/lib/utils'
import type { SocialAccount } from '@/types'

export type PreviewIdentity = {
  name: string
  handle: string
  avatarUrl?: string | null
  headline?: string | null
  isOrg: boolean
  accountId: string
}

export function isOrgAccount(account: SocialAccount): boolean {
  const accountType =
    account.account_type || (account.meta_data?.account_type as string | undefined)
  return accountType === 'organization'
}

export function preferredAccount(
  accounts: SocialAccount[],
  platform: string
): SocialAccount | undefined {
  const list = accounts.filter((a) => a.platform === platform && (!a.status || a.status === 'active'))
  if (list.length === 0) return undefined
  if (platform === 'linkedin') {
    return list.find((a) => isOrgAccount(a)) || list[0]
  }
  return list[0]
}

/** Build preview identity strictly from a connected SocialAccount — no fake names. */
export function identityFromAccount(account: SocialAccount): PreviewIdentity {
  const name = (account.display_name || account.username || '').trim()
  const handle = (account.username || account.account_id || '').trim()
  const isOrg = isOrgAccount(account)
  return {
    name: name || handle || `Account ${account.id.slice(0, 8)}`,
    handle: handle || name || account.account_id,
    avatarUrl: account.avatar_url,
    headline: isOrg ? 'Company Page' : null,
    isOrg,
    accountId: account.id,
  }
}

export function ObjectUrlImage({
  file,
  className,
  alt = '',
}: {
  file: File
  className?: string
  alt?: string
}) {
  const url = useMemo(() => URL.createObjectURL(file), [file])
  useEffect(() => () => URL.revokeObjectURL(url), [url])
  if (!isSafeImageUrl(url)) return null
  return <img src={url} alt={alt} className={className} />
}

export function AccountAvatar({
  identity,
  className,
  fallbackClass,
}: {
  identity: PreviewIdentity
  className?: string
  fallbackClass?: string
}) {
  if (isSafeImageUrl(identity.avatarUrl)) {
    return <img src={identity.avatarUrl!} alt={identity.name} className={cn('object-cover', className)} />
  }
  const initials = identity.name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]?.toUpperCase() || '')
    .join('') || '?'
  return (
    <div className={cn('flex items-center justify-center font-bold', className, fallbackClass)}>
      {initials}
    </div>
  )
}

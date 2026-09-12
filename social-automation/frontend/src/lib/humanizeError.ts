import type { AxiosError } from 'axios'

type ApiDetailShape =
  | { detail?: unknown; message?: unknown }
  | { error?: unknown; errors?: unknown }
  | unknown

function asNonEmptyString(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const trimmed = value.trim()
  return trimmed.length ? trimmed : undefined
}

function tryGetDetailFromData(data: ApiDetailShape): string | undefined {
  if (!data || typeof data !== 'object') return undefined
  const rec = data as Record<string, unknown>

  return (
    asNonEmptyString(rec.detail) ??
    asNonEmptyString(rec.message) ??
    asNonEmptyString(rec.error)
  )
}

export function getAxiosErrorDetail(err: unknown): string | undefined {
  const e = err as AxiosError | undefined

  // Axios error with API response body
  const fromData = tryGetDetailFromData((e as any)?.response?.data)
  if (fromData) return fromData

  // Axios error message
  const fromMessage = asNonEmptyString((e as any)?.message)
  if (fromMessage) return fromMessage

  // Generic error
  if (err && typeof err === 'object' && 'message' in (err as any)) {
    const m = asNonEmptyString((err as any).message)
    if (m) return m
  }

  return undefined
}

function clampText(text: string, maxLen: number): string {
  if (text.length <= maxLen) return text
  return text.slice(0, Math.max(0, maxLen - 1)).trimEnd() + '…'
}

/**
 * Toast-friendly error copy:
 * - human message first (what happened / what to do)
 * - technical detail second (when available), trimmed to keep the toast readable
 */
export function formatErrorToast(humanMessage: string, err?: unknown): string {
  const detail = err ? getAxiosErrorDetail(err) : undefined
  if (!detail) return humanMessage
  if (detail.toLowerCase() === humanMessage.toLowerCase()) return humanMessage
  return `${humanMessage}\nDetails: ${clampText(detail, 180)}`
}


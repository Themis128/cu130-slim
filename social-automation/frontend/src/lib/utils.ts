import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/** App display / scheduling timezone (Greece). API stores UTC. */
export const APP_TIMEZONE =
  (typeof process !== 'undefined' && process.env.NEXT_PUBLIC_APP_TIMEZONE) ||
  'Europe/Athens'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatDate(date: string | Date, options?: Intl.DateTimeFormatOptions): string {
  return new Date(date).toLocaleDateString('en-GB', {
    timeZone: APP_TIMEZONE,
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    ...options,
  })
}

export function formatDateTime(date: string | Date, options?: Intl.DateTimeFormatOptions): string {
  return new Date(date).toLocaleString('en-GB', {
    timeZone: APP_TIMEZONE,
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    ...options,
  })
}

export function formatTime(date: string | Date): string {
  return new Date(date).toLocaleTimeString('en-GB', {
    timeZone: APP_TIMEZONE,
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/** Calendar date key (yyyy-MM-dd) in Europe/Athens. */
export function athensDateKey(date: string | Date): string {
  return toAthensDateTimeLocal(date).slice(0, 10)
}

/** True when both instants fall on the same Athens calendar day. */
export function isSameAthensDay(a: string | Date, b: string | Date): boolean {
  return athensDateKey(a) === athensDateKey(b)
}

/**
 * Match a UTC ISO timestamp to a calendar day cell.
 * Day cells are date-only UI; their Y-M-D is treated as Europe/Athens.
 */
export function isOnAthensCalendarDay(iso: string | Date, day: Date): boolean {
  const y = day.getFullYear()
  const m = String(day.getMonth() + 1).padStart(2, '0')
  const d = String(day.getDate()).padStart(2, '0')
  return athensDateKey(iso) === `${y}-${m}-${d}`
}

/** Keep Athens wall-clock time while moving to another calendar day (from date picker). */
export function moveToAthensDay(iso: string | Date, targetDay: Date): string {
  const timePart = toAthensDateTimeLocal(iso).slice(11, 16) // HH:mm
  // Calendar day cells are constructed in local browser midnight; use their Y-M-D parts
  // as the Athens date the user clicked (UI is Athens-labelled).
  const y = targetDay.getFullYear()
  const m = String(targetDay.getMonth() + 1).padStart(2, '0')
  const d = String(targetDay.getDate()).padStart(2, '0')
  return athensDateTimeLocalToIso(`${y}-${m}-${d}T${timePart}`)
}

/** Value for <input type="datetime-local"> as Europe/Athens wall clock. */
export function toAthensDateTimeLocal(date: string | Date): string {
  const d = new Date(date)
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat('en-CA', {
      timeZone: APP_TIMEZONE,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    })
      .formatToParts(d)
      .filter((p) => p.type !== 'literal')
      .map((p) => [p.type, p.value])
  ) as Record<string, string>
  return `${parts.year}-${parts.month}-${parts.day}T${parts.hour}:${parts.minute}`
}

/** Parse datetime-local wall clock in Europe/Athens → ISO UTC. */
export function athensDateTimeLocalToIso(local: string): string {
  if (!local) return ''
  const [datePart, timePart = '00:00'] = local.split('T')
  const [y, m, d] = datePart.split('-').map(Number)
  const [hh, mm] = timePart.split(':').map(Number)
  let guess = Date.UTC(y, m - 1, d, hh, mm, 0)
  for (let i = 0; i < 3; i++) {
    const asAthens = toAthensDateTimeLocal(new Date(guess))
    const [ad, at] = asAthens.split('T')
    const [ay, am, aday] = ad.split('-').map(Number)
    const [ahh, amm] = at.split(':').map(Number)
    const targetMs = Date.UTC(y, m - 1, d, hh, mm)
    const actualMs = Date.UTC(ay, am - 1, aday, ahh, amm)
    guess += targetMs - actualMs
  }
  return new Date(guess).toISOString()
}

export function formatRelativeTime(date: string | Date): string {
  const now = new Date()
  const then = new Date(date)
  const diffMs = now.getTime() - then.getTime()
  const diffSecs = Math.floor(diffMs / 1000)
  const diffMins = Math.floor(diffSecs / 60)
  const diffHours = Math.floor(diffMins / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffSecs < 60) return 'just now'
  if (diffMins < 60) return `${diffMins}m ago`
  if (diffHours < 24) return `${diffHours}h ago`
  if (diffDays < 7) return `${diffDays}d ago`
  return formatDate(date)
}

export function truncate(str: string, length: number): string {
  if (str.length <= length) return str
  return str.slice(0, length) + '...'
}

export function generateId(): string {
  return crypto.randomUUID().replace(/-/g, "").substring(0, 13);
}
export function debounce<T extends (...args: unknown[]) => unknown>(
  func: T,
  wait: number
): (...args: Parameters<T>) => void {
  let timeout: NodeJS.Timeout | null = null
  return (...args: Parameters<T>) => {
    if (timeout) clearTimeout(timeout)
    timeout = setTimeout(() => func(...args), wait)
  }
}

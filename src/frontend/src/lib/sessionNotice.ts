export const SESSION_NOTICE_REASONS = [
  'TOKEN_EXPIRED',
  'REFRESH_EXPIRED',
  'OTHER_DEVICE_LOGIN',
  'PASSWORD_CHANGED',
  'ACCOUNT_DISABLED',
  'TOKEN_REUSED',
  'INVALID_TOKEN',
  'TOKEN_BLACKLISTED',
] as const

export type SessionNoticeReason = (typeof SESSION_NOTICE_REASONS)[number]

const SESSION_INVALID_REASONS = new Set<SessionNoticeReason>([
  'OTHER_DEVICE_LOGIN',
  'PASSWORD_CHANGED',
  'ACCOUNT_DISABLED',
  'TOKEN_REUSED',
  'INVALID_TOKEN',
  'TOKEN_BLACKLISTED',
])

const SESSION_REASON_MESSAGE_KEYS: Record<SessionNoticeReason, string> = {
  TOKEN_EXPIRED: 'login.sessionExpired',
  REFRESH_EXPIRED: 'login.sessionExpired',
  OTHER_DEVICE_LOGIN: 'login.sessionOtherDevice',
  PASSWORD_CHANGED: 'login.sessionPasswordChanged',
  ACCOUNT_DISABLED: 'login.sessionAccountDisabled',
  TOKEN_REUSED: 'login.sessionTokenReused',
  INVALID_TOKEN: 'login.sessionInvalid',
  TOKEN_BLACKLISTED: 'login.sessionInvalid',
}

const SESSION_NOTICE_STORAGE_KEY = 'hyperfilelens:auth-session-notice'
const SESSION_NOTICE_CONSUMED_KEY = 'hyperfilelens:auth-session-notice-consumed'
const SESSION_NOTICE_COOKIE_KEY = 'hfl_auth_session_notice'
const SESSION_NOTICE_CHANNEL = 'hyperfilelens:auth-session-events'
const SESSION_NOTICE_VERSION = 1
export const SESSION_NOTICE_TTL_MS = 60_000

type StoredSessionNotice = {
  version: typeof SESSION_NOTICE_VERSION
  reason: SessionNoticeReason
  createdAt: number
}

const SESSION_REASON_PRIORITY: Record<SessionNoticeReason, number> = {
  TOKEN_EXPIRED: 1,
  REFRESH_EXPIRED: 2,
  OTHER_DEVICE_LOGIN: 3,
  PASSWORD_CHANGED: 3,
  ACCOUNT_DISABLED: 3,
  TOKEN_REUSED: 3,
  INVALID_TOKEN: 3,
  TOKEN_BLACKLISTED: 3,
}

function browserSessionStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

export function isSessionNoticeReason(reason: unknown): reason is SessionNoticeReason {
  return typeof reason === 'string' && SESSION_NOTICE_REASONS.includes(reason as SessionNoticeReason)
}

export function isSessionInvalidReason(reason: unknown): reason is SessionNoticeReason {
  return isSessionNoticeReason(reason) && SESSION_INVALID_REASONS.has(reason)
}

export function sessionNoticeMessageKey(reason: unknown): string | null {
  return isSessionNoticeReason(reason) ? SESSION_REASON_MESSAGE_KEYS[reason] : null
}

function parseStoredNotice(raw: string | null, now: number): StoredSessionNotice | null {
  if (!raw) return null
  try {
    const notice = JSON.parse(raw) as Partial<StoredSessionNotice>
    if (notice.version !== SESSION_NOTICE_VERSION) return null
    if (!isSessionNoticeReason(notice.reason)) return null
    if (typeof notice.createdAt !== 'number' || !Number.isFinite(notice.createdAt)) return null
    if (notice.createdAt > now || now - notice.createdAt > SESSION_NOTICE_TTL_MS) return null
    return notice as StoredSessionNotice
  } catch {
    return null
  }
}

function readSharedNotice(now = Date.now()): StoredSessionNotice | null {
  if (typeof document === 'undefined') return null
  const prefix = `${SESSION_NOTICE_COOKIE_KEY}=`
  const raw = document.cookie
    .split(';')
    .map(value => value.trim())
    .find(value => value.startsWith(prefix))
    ?.slice(prefix.length)
  if (!raw) return null
  try {
    return parseStoredNotice(decodeURIComponent(raw), now)
  } catch {
    return null
  }
}

function writeSharedNotice(notice: StoredSessionNotice): void {
  if (typeof document === 'undefined') return
  const secure = typeof window !== 'undefined' && window.location.protocol === 'https:'
    ? '; Secure'
    : ''
  document.cookie = `${SESSION_NOTICE_COOKIE_KEY}=${encodeURIComponent(JSON.stringify(notice))}; Path=/; Max-Age=${Math.ceil(SESSION_NOTICE_TTL_MS / 1000)}; SameSite=Lax${secure}`
}

export function clearSharedSessionNotice(): void {
  if (typeof document === 'undefined') return
  document.cookie = `${SESSION_NOTICE_COOKIE_KEY}=; Path=/; Max-Age=0; SameSite=Lax`
}

function broadcastSessionNotice(notice: StoredSessionNotice): void {
  if (typeof window === 'undefined' || typeof window.BroadcastChannel === 'undefined') return
  try {
    const channel = new window.BroadcastChannel(SESSION_NOTICE_CHANNEL)
    if (!(channel instanceof window.EventTarget)) {
      channel.close()
      return
    }
    channel.postMessage(notice)
    channel.close()
  } catch {
    // The shared host cookie and server checks remain available as fallbacks.
  }
}

export function subscribeSessionNotice(
  listener: (reason: SessionNoticeReason) => void,
): () => void {
  if (typeof window === 'undefined' || typeof window.BroadcastChannel === 'undefined') {
    return () => undefined
  }
  try {
    const channel = new window.BroadcastChannel(SESSION_NOTICE_CHANNEL)
    if (!(channel instanceof window.EventTarget)) {
      channel.close()
      return () => undefined
    }
    channel.onmessage = (event: MessageEvent<unknown>) => {
      const notice = event.data && typeof event.data === 'object'
        ? event.data as Partial<StoredSessionNotice>
        : null
      if (!notice) return
      const parsed = parseStoredNotice(JSON.stringify(notice), Date.now())
      if (parsed) listener(parsed.reason)
    }
    return () => channel.close()
  } catch {
    return () => undefined
  }
}

export function storeSessionNotice(
  reason: unknown,
  storage: Storage | null = browserSessionStorage(),
  now = Date.now(),
  publish = true,
): boolean {
  if (!storage || !isSessionNoticeReason(reason)) return false

  let notice: StoredSessionNotice = {
    version: SESSION_NOTICE_VERSION,
    reason,
    createdAt: now,
  }

  const shared = readSharedNotice(now)
  if (shared && SESSION_REASON_PRIORITY[shared.reason] > SESSION_REASON_PRIORITY[reason]) {
    notice = shared
  }

  try {
    storage.setItem(SESSION_NOTICE_STORAGE_KEY, JSON.stringify(notice))
    writeSharedNotice(notice)
    if (publish) broadcastSessionNotice(notice)
    return true
  } catch {
    return false
  }
}

export function consumeSessionNotice(
  storage: Storage | null = browserSessionStorage(),
  now = Date.now(),
): SessionNoticeReason | null {
  if (!storage) return readSharedNotice(now)?.reason ?? null

  let raw: string | null = null
  try {
    raw = storage.getItem(SESSION_NOTICE_STORAGE_KEY)
    storage.removeItem(SESSION_NOTICE_STORAGE_KEY)
  } catch {
    return null
  }
  const local = parseStoredNotice(raw, now)
  const shared = readSharedNotice(now)
  let selected = local
  if (
    shared
    && (
      !selected
      || SESSION_REASON_PRIORITY[shared.reason] > SESSION_REASON_PRIORITY[selected.reason]
      || (
        SESSION_REASON_PRIORITY[shared.reason] === SESSION_REASON_PRIORITY[selected.reason]
        && shared.createdAt > selected.createdAt
      )
    )
  ) {
    selected = shared
  }
  if (!selected) return null

  const fingerprint = `${selected.createdAt}:${selected.reason}`
  try {
    if (!local && storage.getItem(SESSION_NOTICE_CONSUMED_KEY) === fingerprint) return null
    storage.setItem(SESSION_NOTICE_CONSUMED_KEY, fingerprint)
  } catch {
    // Returning the verified notice is safer than dropping it when storage fails.
  }
  return selected.reason
}

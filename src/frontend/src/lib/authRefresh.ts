import { unwrapProblemDetails } from './errors'
import { getCorrelationHeaders } from './requestContext'

export type RefreshTokenResult = {
  ok: boolean
  errorCode?: string
  status?: number
  networkError?: boolean
  retryable?: boolean
}

const API_BASE = import.meta.env.VITE_API_BASE?.toString() || ''
const REFRESH_LOCK_NAME = 'hyperfilelens:auth-refresh'
const REFRESH_LEASE_KEY = 'hyperfilelens:auth-refresh-lease'
const REFRESH_CHANNEL_NAME = 'hyperfilelens:auth-refresh-events'
const REFRESH_LEASE_TTL_MS = 8_000
const PEER_REFRESH_WAIT_MS = 5_000

let refreshPromise: Promise<RefreshTokenResult> | null = null

type RefreshLease = {
  owner: string
  expiresAt: number
}

type SessionProbe = {
  authenticated: boolean
  refreshAvailable: boolean
}

async function readBody(res: Response) {
  const text = await res.text()
  try {
    return text ? JSON.parse(text) : null
  } catch {
    return text
  }
}

function extractRefreshErrorCode(data: unknown): string | undefined {
  const problem = unwrapProblemDetails(data)
  if (problem?.code && typeof problem.code === 'string') return problem.code
  if (problem?.error_code && typeof problem.error_code === 'string') return problem.error_code

  if (data && typeof data === 'object') {
    const d = data as Record<string, unknown>
    if ('data' in d && typeof d.data === 'object') {
      const inner = d.data as Record<string, unknown>
      if (typeof inner.code === 'string' && inner.code.includes('.')) return inner.code
      if (typeof inner.error_code === 'string') return inner.error_code
      if ('error' in inner && typeof inner.error === 'object') {
        const err = inner.error as Record<string, unknown>
        if (typeof err.error_code === 'string') return err.error_code
      }
    }
    if ('error' in d && typeof d.error === 'object') {
      const err = d.error as Record<string, unknown>
      if (typeof err.error_code === 'string') return err.error_code
    }
    if (typeof d.error_code === 'string') return d.error_code
  }
  return undefined
}

function parseSessionProbe(data: unknown): SessionProbe {
  const outer = data && typeof data === 'object' ? data as Record<string, unknown> : {}
  const inner = outer.data && typeof outer.data === 'object'
    ? outer.data as Record<string, unknown>
    : outer
  return {
    authenticated: inner.authenticated === true,
    refreshAvailable: inner.refresh_available === true,
  }
}

async function inspectSession(): Promise<SessionProbe | null> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/auth/token/refresh`, {
      credentials: 'include',
      headers: getCorrelationHeaders(),
      cache: 'no-store',
    })
    if (!res.ok) return null
    return parseSessionProbe(await readBody(res))
  } catch {
    return null
  }
}

function broadcastRefreshComplete(): void {
  if (typeof window === 'undefined' || typeof window.BroadcastChannel === 'undefined') return
  try {
    const channel = new window.BroadcastChannel(REFRESH_CHANNEL_NAME)
    if (!(channel instanceof window.EventTarget)) {
      channel.close()
      return
    }
    channel.postMessage({ type: 'refresh-complete', createdAt: Date.now() })
    channel.close()
  } catch {
    // Same-origin coordination is best effort; the backend remains authoritative.
  }
}

function waitForRefreshSignal(timeoutMs: number): Promise<void> {
  if (typeof window === 'undefined' || typeof window.BroadcastChannel === 'undefined') {
    return new Promise(resolve => setTimeout(resolve, timeoutMs))
  }
  return new Promise((resolve) => {
    let settled = false
    const channel = new window.BroadcastChannel(REFRESH_CHANNEL_NAME)
    if (!(channel instanceof window.EventTarget)) {
      channel.close()
      window.setTimeout(resolve, timeoutMs)
      return
    }
    const finish = () => {
      if (settled) return
      settled = true
      channel.close()
      resolve()
    }
    channel.onmessage = finish
    window.setTimeout(finish, timeoutMs)
  })
}

async function waitForPeerRefresh(): Promise<RefreshTokenResult> {
  const deadline = Date.now() + PEER_REFRESH_WAIT_MS
  while (Date.now() < deadline) {
    await waitForRefreshSignal(150)
    const session = await inspectSession()
    if (session?.authenticated) return { ok: true, status: 200 }
    if (session && !session.refreshAvailable) {
      return { ok: false, status: 401, errorCode: 'REFRESH_EXPIRED' }
    }
  }
  return {
    ok: false,
    status: 409,
    errorCode: 'REFRESH_CONCURRENT',
    retryable: true,
  }
}

async function requestTokenRefresh(): Promise<RefreshTokenResult> {
  try {
    const res = await fetch(`${API_BASE}/api/v1/auth/token/refresh`, {
      method: 'POST',
      credentials: 'include',
      headers: getCorrelationHeaders(),
    })
    if (res.ok) return { ok: true, status: res.status }
    const data = await readBody(res)
    const errorCode = extractRefreshErrorCode(data)
    if (res.status === 409 && errorCode === 'REFRESH_CONCURRENT') {
      return waitForPeerRefresh()
    }
    return { ok: false, errorCode, status: res.status }
  } catch {
    // Security-first recovery: the server may already have consumed the old
    // refresh token even though this response was lost. Do not replay the POST
    // automatically. Keep local state for now; a later explicit attempt lets
    // the backend confirm whether the session is still valid or must sign in.
    return { ok: false, networkError: true }
  } finally {
    broadcastRefreshComplete()
  }
}

function browserLocalStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.localStorage
  } catch {
    return null
  }
}

function createLeaseOwner(): string {
  try {
    return crypto.randomUUID()
  } catch {
    return `${Date.now()}-${Math.random()}`
  }
}

function readLease(storage: Storage): RefreshLease | null {
  try {
    const raw = storage.getItem(REFRESH_LEASE_KEY)
    if (!raw) return null
    const lease = JSON.parse(raw) as Partial<RefreshLease>
    if (typeof lease.owner !== 'string' || typeof lease.expiresAt !== 'number') return null
    return { owner: lease.owner, expiresAt: lease.expiresAt }
  } catch {
    return null
  }
}

function acquireLease(storage: Storage, owner: string): boolean {
  const current = readLease(storage)
  if (current && current.expiresAt > Date.now()) return false
  try {
    storage.setItem(REFRESH_LEASE_KEY, JSON.stringify({
      owner,
      expiresAt: Date.now() + REFRESH_LEASE_TTL_MS,
    }))
    return readLease(storage)?.owner === owner
  } catch {
    return false
  }
}

function releaseLease(storage: Storage, owner: string): void {
  try {
    if (readLease(storage)?.owner === owner) storage.removeItem(REFRESH_LEASE_KEY)
  } catch {
    // Ignore storage cleanup failures; the lease has a finite expiry.
  }
}

async function refreshAfterSessionCheck(): Promise<RefreshTokenResult> {
  const session = await inspectSession()
  if (session?.authenticated) return { ok: true, status: 200 }
  return requestTokenRefresh()
}

async function coordinatedRefresh(): Promise<RefreshTokenResult> {
  if (typeof navigator !== 'undefined' && navigator.locks) {
    return navigator.locks.request(REFRESH_LOCK_NAME, refreshAfterSessionCheck)
  }

  const storage = browserLocalStorage()
  if (!storage) return requestTokenRefresh()

  const owner = createLeaseOwner()
  if (!acquireLease(storage, owner)) {
    const peerResult = await waitForPeerRefresh()
    if (peerResult.ok || !peerResult.retryable) return peerResult
    if (!acquireLease(storage, owner)) return peerResult
  }

  try {
    return await refreshAfterSessionCheck()
  } finally {
    releaseLease(storage, owner)
  }
}

export function refreshAuthToken(): Promise<RefreshTokenResult> {
  if (!refreshPromise) {
    refreshPromise = coordinatedRefresh().finally(() => {
      refreshPromise = null
    })
  }
  return refreshPromise
}

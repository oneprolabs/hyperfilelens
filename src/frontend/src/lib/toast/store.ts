import { reactive, readonly } from 'vue'
import type { ErrorDetailsPayload } from '../errors/details'

export type ToastType = 'success' | 'info' | 'warning' | 'error'

export type ToastOptions = {
  type: ToastType
  title?: string
  message: string
  dedupeKey?: string
  duration?: number
  copyText?: string
  details?: ErrorDetailsPayload
  action?: { label: string; onClick: () => void | Promise<void> }
  onClose?: () => void
}

export type ToastRecord = ToastOptions & {
  id: string
  dedupeKey: string
  duration: number
  remainingMs: number
  repeatCount: number
  paused: boolean
  lastTickAt: number
  createdAt: number
}

const DEFAULT_DURATION: Record<ToastType, number> = {
  success: 3000,
  info: 4000,
  warning: 6000,
  error: 8000,
}

const MAX_VISIBLE = 4
const state = reactive<{ items: ToastRecord[] }>({ items: [] })
let sequence = 0
let ticker: ReturnType<typeof setInterval> | undefined

export const toastState = readonly(state)

function now() {
  return Date.now()
}

function stopTickerIfIdle() {
  if (state.items.length || !ticker) return
  clearInterval(ticker)
  ticker = undefined
}

function tick() {
  const current = now()
  const expired: string[] = []
  for (const item of state.items) {
    if (item.paused || item.duration === 0) continue
    const elapsed = Math.max(0, current - item.lastTickAt)
    item.lastTickAt = current
    item.remainingMs = Math.max(0, item.remainingMs - elapsed)
    if (item.remainingMs === 0) expired.push(item.id)
  }
  expired.forEach(closeToast)
}

function ensureTicker() {
  if (ticker || typeof window === 'undefined') return
  ticker = window.setInterval(tick, 50)
}

function defaultDedupeKey(options: ToastOptions) {
  return `${options.type}:${options.title || ''}:${options.message}`
}

function removeOverflow() {
  while (state.items.length >= MAX_VISIBLE) {
    const disposableIndex = state.items.findIndex((item) => item.type === 'success' || item.type === 'info')
    const item = state.items[disposableIndex >= 0 ? disposableIndex : 0]
    if (!item) return
    closeToast(item.id)
  }
}

export function pushToast(options: ToastOptions) {
  const dedupeKey = options.dedupeKey || defaultDedupeKey(options)
  const existing = state.items.find((item) => item.dedupeKey === dedupeKey)
  const duration = options.duration ?? DEFAULT_DURATION[options.type]
  const current = now()

  if (existing) {
    existing.title = options.title
    existing.message = options.message
    existing.copyText = options.copyText
    existing.details = options.details
    existing.action = options.action
    existing.duration = duration
    existing.remainingMs = duration
    existing.repeatCount += 1
    existing.lastTickAt = current
    return { close: () => closeToast(existing.id) }
  }

  removeOverflow()
  const record: ToastRecord = {
    ...options,
    id: `hfl-toast-${++sequence}`,
    dedupeKey,
    duration,
    remainingMs: duration,
    repeatCount: 1,
    paused: false,
    lastTickAt: current,
    createdAt: current,
  }
  state.items.unshift(record)
  ensureTicker()
  return { close: () => closeToast(record.id) }
}

export function closeToast(id: string) {
  const index = state.items.findIndex((item) => item.id === id)
  if (index < 0) return
  const [item] = state.items.splice(index, 1)
  item?.onClose?.()
  stopTickerIfIdle()
}

export function pauseToast(id: string) {
  const item = state.items.find((candidate) => candidate.id === id)
  if (!item || item.paused) return
  const current = now()
  if (item.duration !== 0) {
    item.remainingMs = Math.max(0, item.remainingMs - Math.max(0, current - item.lastTickAt))
  }
  item.lastTickAt = current
  item.paused = true
}

export function resumeToast(id: string) {
  const item = state.items.find((candidate) => candidate.id === id)
  if (!item || !item.paused) return
  item.paused = false
  item.lastTickAt = now()
  ensureTicker()
}

export function closeAllToasts() {
  ;[...state.items].forEach((item) => closeToast(item.id))
}

export function resetToastStoreForTests() {
  closeAllToasts()
  sequence = 0
}

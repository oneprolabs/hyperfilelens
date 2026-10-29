import { computed, ref, watch } from 'vue'

export const CHAT_PREFERENCES_KEY = 'hfl.copilot.chatPreferences.v1'
export const CHAT_PREFERENCES_LIMIT = 100
export const CHAT_PREFERENCES_TTL_MS = 30 * 24 * 60 * 60 * 1000
const MAX_STORAGE_LENGTH = 64 * 1024

type ChatScope = { userId: number; orgKey: string; chatId: number }
type Selection = { showCitations: boolean; deepThinking: boolean }
type Entry = ChatScope & Selection & { updatedAt: number }

function isId(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0
}

function sameScope(left: ChatScope, right: ChatScope) {
  return left.userId === right.userId && left.orgKey === right.orgKey && left.chatId === right.chatId
}

function prune(raw: unknown): Entry[] {
  if (!Array.isArray(raw)) return []
  const now = Date.now()
  const entries: Entry[] = []
  for (const row of raw) {
    if (!row || !isId(row.userId) || !isId(row.chatId)
      || typeof row.orgKey !== 'string' || !row.orgKey.trim() || row.orgKey.length > 256
      || typeof row.showCitations !== 'boolean' || typeof row.deepThinking !== 'boolean'
      || !Number.isFinite(row.updatedAt) || row.updatedAt > now
      || now - row.updatedAt >= CHAT_PREFERENCES_TTL_MS) continue
    // Copy only the small, allowlisted preference fields.
    entries.push({
      userId: row.userId, orgKey: row.orgKey, chatId: row.chatId,
      showCitations: row.showCitations, deepThinking: row.deepThinking, updatedAt: row.updatedAt,
    })
  }
  return entries.sort((a, b) => b.updatedAt - a.updatedAt)
    .filter((entry, index, all) => all.findIndex(other => sameScope(entry, other)) === index)
    .slice(0, CHAT_PREFERENCES_LIMIT)
}

function readEntries(fallback: Entry[]): Entry[] {
  try {
    const raw = localStorage.getItem(CHAT_PREFERENCES_KEY)
    return raw && raw.length <= MAX_STORAGE_LENGTH ? prune(JSON.parse(raw)) : []
  } catch {
    // Keep choices usable in memory when storage is unavailable.
    return prune(fallback)
  }
}

function persist(entries: Entry[]): boolean {
  try {
    if (entries.length) localStorage.setItem(CHAT_PREFERENCES_KEY, JSON.stringify(entries))
    else localStorage.removeItem(CHAT_PREFERENCES_KEY)
    return true
  } catch {
    // Storage restrictions or quota errors must never block chat.
    return false
  }
}

/** Browser-local choices, isolated by user, organization and Chat; no SL configuration writes. */
export function useChatComposerPreferences(context: {
  userId: () => number | null
  orgKey: () => string
  chatId: () => number | null
  defaultDeepThinking: () => boolean
}) {
  const entries = ref<Entry[]>([])
  let pendingWrite = false
  function load() {
    return pendingWrite ? prune(entries.value) : readEntries(entries.value)
  }
  function save() {
    pendingWrite = !persist(entries.value)
  }
  const scope = computed<ChatScope | null>(() => {
    const userId = context.userId()
    const orgKey = context.orgKey().trim()
    const chatId = context.chatId()
    return isId(userId) && orgKey && orgKey.length <= 256 && isId(chatId)
      ? { userId, orgKey, chatId } : null
  })
  const transient = ref<Selection>({ showCitations: true, deepThinking: false })

  watch(
    [context.userId, context.orgKey, context.chatId],
    () => {
      transient.value = { showCitations: true, deepThinking: context.defaultDeepThinking() }
      entries.value = load()
      save() // Remove expired/invalid entries on access; no background timer.
    },
    { immediate: true, flush: 'sync' },
  )

  const selection = computed<Selection>(() =>
    (scope.value && entries.value.find(entry => sameScope(entry, scope.value!)))
    || (scope.value
      ? { showCitations: true, deepThinking: context.defaultDeepThinking() }
      : transient.value),
  )

  function update(patch: Partial<Selection>) {
    const value = { ...selection.value, ...patch }
    if (!scope.value) {
      transient.value = value
      return
    }
    const entry: Entry = { ...scope.value, ...value, updatedAt: Date.now() }
    entries.value = prune([
      entry,
      ...load().filter(other => !sameScope(entry, other)),
    ])
    save()
  }

  function forgetChatPreferences(chatId: number) {
    const current = scope.value
    if (!current) return
    const target = { ...current, chatId }
    entries.value = load().filter(entry => !sameScope(entry, target))
    save()
  }

  return {
    showCitations: computed({
      get: () => selection.value.showCitations,
      set: (value: boolean) => update({ showCitations: value }),
    }),
    deepThinking: computed({
      get: () => selection.value.deepThinking,
      set: (value: boolean) => update({ deepThinking: value }),
    }),
    forgetChatPreferences,
  }
}

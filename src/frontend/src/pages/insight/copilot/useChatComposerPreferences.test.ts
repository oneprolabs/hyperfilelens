// @vitest-environment jsdom

import { effectScope, nextTick, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CHAT_PREFERENCES_KEY,
  CHAT_PREFERENCES_LIMIT,
  CHAT_PREFERENCES_TTL_MS,
  useChatComposerPreferences,
} from './useChatComposerPreferences'

describe('Chat-scoped composer preferences', () => {
  const scopes: ReturnType<typeof effectScope>[] = []
  function preferences() {
    const userId = ref<number | null>(7)
    const orgKey = ref('org-a')
    const chatId = ref<number | null>(1)
    const defaultDeepThinking = ref(false)
    const scope = effectScope()
    scopes.push(scope)
    const choices = scope.run(() => useChatComposerPreferences({
      userId: () => userId.value,
      orgKey: () => orgKey.value,
      chatId: () => chatId.value,
      defaultDeepThinking: () => defaultDeepThinking.value,
    }))!
    return { ...choices, userId, orgKey, chatId, defaultDeepThinking }
  }

  function entry(chatId: number, updatedAt = Date.now()) {
    return { userId: 7, orgKey: 'org-a', chatId, showCitations: false, deepThinking: true, updatedAt }
  }

  beforeEach(() => localStorage.clear())
  afterEach(() => {
    scopes.splice(0).forEach(scope => scope.stop())
    vi.restoreAllMocks()
    vi.useRealTimers()
    localStorage.clear()
  })

  it('defaults to citations on and deep thinking off without inheriting the old global preference', () => {
    localStorage.setItem('hfl.copilot.showCitations', 'false')
    const choices = preferences()
    expect(choices.showCitations.value).toBe(true)
    expect(choices.deepThinking.value).toBe(false)
    expect(localStorage.getItem(CHAT_PREFERENCES_KEY)).toBeNull()
  })

  it('remembers both choices independently for each Chat, including after remounting', async () => {
    const choices = preferences()
    choices.showCitations.value = false
    choices.deepThinking.value = true
    choices.chatId.value = 2
    await nextTick()
    expect(choices.showCitations.value).toBe(true)
    expect(choices.deepThinking.value).toBe(false)
    choices.deepThinking.value = true

    choices.chatId.value = 1
    await nextTick()
    expect(choices.showCitations.value).toBe(false)
    expect(choices.deepThinking.value).toBe(true)
    const restored = preferences()
    expect(restored.showCitations.value).toBe(false)
    expect(restored.deepThinking.value).toBe(true)
    restored.chatId.value = 2
    expect(restored.showCitations.value).toBe(true)
    expect(restored.deepThinking.value).toBe(true)
  })

  it('does not copy state between organizations or users sharing the same Chat ID', () => {
    const choices = preferences()
    choices.showCitations.value = false
    choices.deepThinking.value = true
    choices.orgKey.value = 'org-b'
    expect(choices.showCitations.value).toBe(true)
    expect(choices.deepThinking.value).toBe(false)
    choices.orgKey.value = 'org-a'
    choices.userId.value = 8
    expect(choices.showCitations.value).toBe(true)
    expect(choices.deepThinking.value).toBe(false)
    choices.userId.value = 7
    expect(choices.showCitations.value).toBe(false)
    expect(choices.deepThinking.value).toBe(true)
  })

  it('uses the saved Chat analysis mode only until a local choice is recorded', () => {
    const choices = preferences()
    choices.defaultDeepThinking.value = true
    expect(choices.deepThinking.value).toBe(true)
    choices.deepThinking.value = false
    choices.defaultDeepThinking.value = false
    choices.defaultDeepThinking.value = true
    expect(choices.deepThinking.value).toBe(false)
  })

  it('removes only the deleted Chat in the current user and organization', () => {
    const choices = preferences()
    choices.deepThinking.value = true
    choices.chatId.value = 2
    choices.showCitations.value = false
    choices.orgKey.value = 'org-b'
    choices.deepThinking.value = true
    choices.orgKey.value = 'org-a'
    choices.forgetChatPreferences(2)
    expect(choices.showCitations.value).toBe(true)
    choices.chatId.value = 1
    expect(choices.deepThinking.value).toBe(true)
    choices.orgKey.value = 'org-b'
    choices.chatId.value = 2
    expect(choices.deepThinking.value).toBe(true)
  })

  it('expires old entries on access without a background timer', () => {
    localStorage.setItem(CHAT_PREFERENCES_KEY, JSON.stringify([
      entry(1, Date.now() - CHAT_PREFERENCES_TTL_MS),
      entry(2),
    ]))
    const choices = preferences()
    expect(choices.showCitations.value).toBe(true)
    expect(JSON.parse(localStorage.getItem(CHAT_PREFERENCES_KEY)!)).toHaveLength(1)
    choices.chatId.value = 2
    expect(choices.deepThinking.value).toBe(true)
  })

  it('bounds the shared store and evicts the oldest choices first', () => {
    localStorage.setItem(CHAT_PREFERENCES_KEY, JSON.stringify(
      Array.from({ length: CHAT_PREFERENCES_LIMIT + 3 }, (_, index) => entry(index + 1, Date.now() - index)),
    ))
    const choices = preferences()
    expect(JSON.parse(localStorage.getItem(CHAT_PREFERENCES_KEY)!)).toHaveLength(CHAT_PREFERENCES_LIMIT)
    choices.chatId.value = 999
    choices.deepThinking.value = true
    const stored = JSON.parse(localStorage.getItem(CHAT_PREFERENCES_KEY)!)
    expect(stored).toHaveLength(CHAT_PREFERENCES_LIMIT)
    expect(stored[0].chatId).toBe(999)
    expect(stored.some((row: { chatId: number }) => row.chatId === CHAT_PREFERENCES_LIMIT)).toBe(false)
  })

  it.each([
    '{invalid',
    '{}',
    JSON.stringify([entry(1, Date.now() + 86400000), { ...entry(2), deepThinking: 'true' }]),
    'x'.repeat(64 * 1024 + 1),
  ])('ignores invalid or oversized storage safely (case %#)', (raw) => {
    localStorage.setItem(CHAT_PREFERENCES_KEY, raw)
    const choices = preferences()
    expect(choices.showCitations.value).toBe(true)
    expect(choices.deepThinking.value).toBe(false)
    expect(localStorage.getItem(CHAT_PREFERENCES_KEY)).toBeNull()
  })

  it('keeps choices in memory when storage is full, including across Chat switches', () => {
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('Quota exceeded') })
    const choices = preferences()
    choices.showCitations.value = false
    choices.deepThinking.value = true
    choices.chatId.value = 2
    expect(choices.deepThinking.value).toBe(false)
    choices.chatId.value = 1
    expect(choices.showCitations.value).toBe(false)
    expect(choices.deepThinking.value).toBe(true)
  })

  it('keeps chat usable when all browser storage access is blocked', () => {
    for (const method of ['getItem', 'setItem', 'removeItem'] as const) {
      vi.spyOn(Storage.prototype, method).mockImplementation(() => { throw new Error('Storage blocked') })
    }
    const choices = preferences()
    choices.deepThinking.value = true
    choices.showCitations.value = false
    choices.chatId.value = 2
    expect(choices.showCitations.value).toBe(true)
    expect(choices.deepThinking.value).toBe(false)
    choices.chatId.value = 1
    expect(choices.showCitations.value).toBe(false)
    expect(choices.deepThinking.value).toBe(true)
    choices.forgetChatPreferences(1)
    expect(choices.showCitations.value).toBe(true)
    expect(choices.deepThinking.value).toBe(false)
  })

  it('does not persist choices without a verified user and organization', () => {
    const choices = preferences()
    choices.userId.value = null
    choices.deepThinking.value = true
    expect(choices.deepThinking.value).toBe(true)
    expect(localStorage.getItem(CHAT_PREFERENCES_KEY)).toBeNull()
    choices.userId.value = 7
    expect(choices.deepThinking.value).toBe(false)
    choices.orgKey.value = ''
    choices.showCitations.value = false
    expect(localStorage.getItem(CHAT_PREFERENCES_KEY)).toBeNull()
  })
})

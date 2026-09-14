import { describe, expect, it } from 'vitest'

import { aiProviderLabel } from './aiProviderDisplay'

describe('aiProviderLabel', () => {
  it('uses a user-facing name for OpenAI-compatible providers', () => {
    expect(aiProviderLabel('openai_compatible')).toBe('OpenAI Compatible')
    expect(aiProviderLabel('OpenAI-Compatible')).toBe('OpenAI Compatible')
  })
})

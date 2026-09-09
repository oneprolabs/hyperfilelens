import { describe, expect, it } from 'vitest'
import { aiProviderIconUrl } from './aiProviderIcons'

describe('aiProviderIconUrl', () => {
  it('maps SourceLens provider ids to local brand icons', () => {
    expect(aiProviderIconUrl('openai')).toBe('/ai-providers/openai.svg')
    expect(aiProviderIconUrl('azure_openai')).toBe('/ai-providers/azure.svg')
    expect(aiProviderIconUrl('dashscope')).toBe('/ai-providers/qwen.svg')
    expect(aiProviderIconUrl('volcengine')).toBe('/ai-providers/doubao.svg')
    expect(aiProviderIconUrl('zai')).toBe('/ai-providers/zhipu.svg')
  })

  it('lets the provider component fall back when no icon is available', () => {
    expect(aiProviderIconUrl('custom_provider')).toBe('')
  })
})

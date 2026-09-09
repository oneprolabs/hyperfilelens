/** Provider brand SVGs mirror the local icon set used by SourceLens. */
const PROVIDER_ICON_SLUGS = new Set([
  'anthropic',
  'aws',
  'azure',
  'deepseek',
  'doubao',
  'gemini',
  'meta',
  'minimax',
  'mistral',
  'moonshot',
  'nvidia',
  'openai',
  'openai-compatible',
  'openrouter',
  'qwen',
  'xai',
  'zhipu',
])

const PROVIDER_ICON_ALIASES: Record<string, string> = {
  amazon_nova: 'aws',
  azure_openai: 'azure',
  openai_compatible: 'openai-compatible',
  claude: 'anthropic',
  dashscope: 'qwen',
  meta_llama: 'meta',
  nvidia_nim: 'nvidia',
  volcengine: 'doubao',
  zai: 'zhipu',
}

function normalizeProvider(provider: string) {
  return provider.trim().toLowerCase()
}

function providerIconSlug(provider: string) {
  const key = normalizeProvider(provider)
  if (!key) return ''

  const alias = PROVIDER_ICON_ALIASES[key]
  if (alias && PROVIDER_ICON_SLUGS.has(alias)) return alias
  if (PROVIDER_ICON_SLUGS.has(key)) return key

  const compactKey = key.replace(/[_\s-]/g, '')
  if (PROVIDER_ICON_SLUGS.has(compactKey)) return compactKey
  if (key.startsWith('azure')) return 'azure'
  if (key.startsWith('meta')) return 'meta'
  if (key.startsWith('amazon')) return 'aws'
  if (key.startsWith('nvidia')) return 'nvidia'
  return ''
}

export function aiProviderIconUrl(provider: string) {
  const slug = providerIconSlug(provider)
  return slug ? `/ai-providers/${slug}.svg` : ''
}

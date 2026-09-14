const PROVIDER_LABELS: Record<string, string> = {
  openai: 'OpenAI',
  azure_openai: 'Azure OpenAI',
  gemini: 'Google Gemini',
  anthropic: 'Anthropic',
  mistral: 'Mistral',
  dashscope: 'DashScope (Qwen)',
  deepseek: 'DeepSeek',
  xai: 'xAI (Grok)',
  minimax: 'MiniMax',
  moonshot: 'Moonshot (Kimi)',
  zai: 'Z.AI (GLM)',
  volcengine: 'Volcengine (Doubao)',
  openrouter: 'OpenRouter',
  openai_compatible: 'OpenAI Compatible',
}

const PROVIDER_COLORS: Record<string, string> = {
  openai: '#412991',
  azure_openai: '#0078D4',
  gemini: '#4285F4',
  anthropic: '#D4A574',
  mistral: '#6366F1',
  dashscope: '#FF6A00',
  deepseek: '#1E40AF',
  xai: '#1a1a1a',
  minimax: '#1E3A5F',
  moonshot: '#6366F1',
  zai: '#2D5AFF',
  volcengine: '#E23B2F',
  openrouter: '#B83280',
  openai_compatible: '#635BFF',
}

export function aiProviderLabel(provider: string, fallback?: string) {
  const key = provider.trim().toLowerCase().replace(/[\s-]+/g, '_')
  return PROVIDER_LABELS[key] || fallback || provider || '—'
}

export function aiProviderColor(provider: string) {
  const key = provider.trim().toLowerCase().replace(/[\s-]+/g, '_')
  return PROVIDER_COLORS[key] || '#64748b'
}

export function aiProviderLetter(provider: string) {
  const label = aiProviderLabel(provider)
  const ch = label.replace(/[^A-Za-z0-9]/g, '').charAt(0)
  return (ch || provider.charAt(0) || '?').toUpperCase()
}

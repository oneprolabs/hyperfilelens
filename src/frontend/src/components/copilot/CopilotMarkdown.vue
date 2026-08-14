<script setup lang="ts">
import { computed } from 'vue'

const props = defineProps<{
  content: string
}>()

function escapeHtml(value: string) {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}

function safeLinkHref(value: string): boolean {
  const normalized = value.trim().toLowerCase()
  return normalized.startsWith('https://')
    || normalized.startsWith('http://')
    || normalized.startsWith('mailto:')
    || normalized.startsWith('/')
    || normalized.startsWith('./')
    || normalized.startsWith('../')
    || normalized.startsWith('#')
}

function renderMarkdown(text: string): string {
  if (!text.trim()) return ''
  let html = escapeHtml(text.replace(/\r\n/g, '\n').replace(/\r/g, '\n'))

  html = html.replace(/```(\w*)\n([\s\S]*?)```/g, (_m, _lang, code) => {
    return `<pre class="copilot-md-pre"><code>${code.trim()}</code></pre>`
  })
  html = html.replace(/`([^`\n]+)`/g, '<code class="copilot-md-code">$1</code>')
  html = html.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
  html = html.replace(
    /\[([^\]]+)\]\(([^)\s]+)\)/g,
    (_match, label: string, href: string) => safeLinkHref(href)
      ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
      : label,
  )

  return html
    .split(/\n{2,}/)
    .map((block) => {
      if (block.includes('<pre class="copilot-md-pre">')) return block
      return `<p>${block.replace(/\n/g, '<br>')}</p>`
    })
    .join('')
}

const rendered = computed(() => renderMarkdown(props.content))
</script>

<template>
  <!-- Content is escaped and link protocols are allowlisted before rendering. -->
  <!-- eslint-disable vue/no-v-html -->
  <div
    class="copilot-markdown"
    v-html="rendered"
  />
  <!-- eslint-enable vue/no-v-html -->
</template>

<style scoped>
.copilot-markdown {
  font-family: var(--font-sans);
  font-size: 15px;
  line-height: 1.65;
  font-weight: 400;
  letter-spacing: normal;
  color: var(--color-text-primary);
  word-break: break-word;
  -webkit-font-smoothing: antialiased;
  -moz-osx-font-smoothing: grayscale;
}

.copilot-markdown :deep(p) {
  margin: 0 0 0.75em;
}

.copilot-markdown :deep(p:last-child) {
  margin-bottom: 0;
}

.copilot-markdown :deep(a) {
  color: var(--color-primary);
  text-decoration: underline;
}

.copilot-markdown :deep(.copilot-md-code) {
  padding: 0.1em 0.35em;
  border-radius: 4px;
  background: rgba(69, 122, 176, 0.12);
  font-family: var(--font-mono);
  font-size: 0.92em;
}

.copilot-markdown :deep(.copilot-md-pre) {
  margin: 0.75em 0;
  padding: 12px 14px;
  overflow-x: auto;
  border-radius: var(--radius-card);
  background: #0f172a;
  color: #e2e8f0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.copilot-markdown :deep(.copilot-md-pre code) {
  background: transparent;
  padding: 0;
}
</style>

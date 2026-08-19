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

function inlineFormat(text: string): string {
  return text
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`\n]+)`/g, '<code class="copilot-md-code">$1</code>')
    .replace(
      /\[([^\]]+)\]\(([^)\s]+)\)/g,
      (_match, label: string, href: string) => safeLinkHref(href)
        ? `<a href="${href}" target="_blank" rel="noopener noreferrer">${label}</a>`
        : label,
    )
}

function parseTableCells(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function isTableBlock(lines: string[]): boolean {
  if (lines.length < 2) return false
  return lines.every((line) => line.trim().startsWith('|') && line.trim().endsWith('|'))
}

function renderTable(block: string): string {
  const lines = block.split('\n').filter((line) => line.trim())
  if (lines.length < 2) return `<p>${inlineFormat(escapeHtml(block.replace(/\n/g, '<br>')))}</p>`

  const separator = lines[1].replace(/\|/g, '').trim()
  if (!separator.match(/^[\s:-]+$/)) {
    return `<p>${inlineFormat(escapeHtml(block.replace(/\n/g, '<br>')))}</p>`
  }

  const headers = parseTableCells(lines[0])
  const rows = lines.slice(2).map(parseTableCells)

  const thead = `<thead><tr>${headers.map((h) => `<th>${inlineFormat(escapeHtml(h))}</th>`).join('')}</tr></thead>`
  const tbody = `<tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${inlineFormat(escapeHtml(cell))}</td>`).join('')}</tr>`).join('')}</tbody>`
  return `<table class="copilot-md-table">${thead}${tbody}</table>`
}

function renderList(block: string, ordered: boolean): string {
  const regex = ordered ? /^\d+\.\s+(.*)$/gm : /^[-*]\s+(.*)$/gm
  const tag = ordered ? 'ol' : 'ul'
  const items: string[] = []
  let match
  while ((match = regex.exec(block)) !== null) {
    items.push(`<li>${inlineFormat(escapeHtml(match[1]))}</li>`)
  }
  return `<${tag} class="copilot-md-${tag}">${items.join('')}</${tag}>`
}

function renderBlock(block: string): string {
  const lines = block.split('\n').filter((line) => line.trim() !== '')

  if (isTableBlock(lines)) {
    return renderTable(block)
  }

  const headingMatch = block.match(/^(#{1,6})\s+(.+)$/)
  if (headingMatch) {
    const level = headingMatch[1].length
    return `<h${level} class="copilot-md-h${level}">${inlineFormat(escapeHtml(headingMatch[2]))}</h${level}>`
  }

  if (/^[-*]\s/m.test(block)) {
    return renderList(block, false)
  }

  if (/^\d+\.\s/m.test(block)) {
    return renderList(block, true)
  }

  if (/^(?:-{3,}|\*{3,}|_{3,})$/.test(block.trim())) {
    return '<hr class="copilot-md-hr">'
  }

  return `<p>${inlineFormat(escapeHtml(block.replace(/\n/g, '<br>')))}</p>`
}

function renderMarkdown(text: string): string {
  if (!text.trim()) return ''
  const normalized = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n')

  const codeBlocks: string[] = []
  const working = normalized.replace(/```(\w*)\n([\s\S]*?)```/g, (_match, _lang, code) => {
    const index = codeBlocks.length
    codeBlocks.push(`<pre class="copilot-md-pre"><code>${escapeHtml(code.trim())}</code></pre>`)
    return `\n\n<!--CODE_BLOCK_${index}-->\n\n`
  })

  const blocks = working.split(/\n{2,}/).filter((block) => block.trim())
  const rendered: string[] = []

  for (const raw of blocks) {
    const block = raw.trim()
    const codeMatch = block.match(/^<!--CODE_BLOCK_(\d+)-->$/)
    if (codeMatch) {
      rendered.push(codeBlocks[parseInt(codeMatch[1], 10)])
      continue
    }
    rendered.push(renderBlock(block))
  }

  return rendered.join('')
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
  color: #2563eb;
  text-decoration: underline;
}

.copilot-markdown :deep(.copilot-md-code) {
  padding: 0.1em 0.35em;
  border-radius: 4px;
  background: rgba(37, 99, 235, 0.1);
  color: #1e40af;
  font-family: var(--font-mono);
  font-size: 0.92em;
}

.copilot-markdown :deep(.copilot-md-pre) {
  margin: 0.75em 0;
  padding: 12px 14px;
  overflow-x: auto;
  border-radius: var(--radius-card);
  background: #1e293b;
  color: #e2e8f0;
  font-family: var(--font-mono);
  font-size: 12px;
  line-height: 1.55;
}

.copilot-markdown :deep(.copilot-md-pre code) {
  background: transparent;
  padding: 0;
  color: inherit;
}

.copilot-markdown :deep(h1),
.copilot-markdown :deep(h2),
.copilot-markdown :deep(h3),
.copilot-markdown :deep(h4),
.copilot-markdown :deep(h5),
.copilot-markdown :deep(h6) {
  margin: 1em 0 0.5em;
  font-weight: 600;
  line-height: 1.35;
  color: #111827;
}

.copilot-markdown :deep(h1) { font-size: 1.5em; }
.copilot-markdown :deep(h2) { font-size: 1.25em; }
.copilot-markdown :deep(h3) { font-size: 1.1em; }
.copilot-markdown :deep(h4) { font-size: 1em; }
.copilot-markdown :deep(h5),
.copilot-markdown :deep(h6) { font-size: 0.95em; }

.copilot-markdown :deep(ul),
.copilot-markdown :deep(ol) {
  margin: 0 0 0.75em;
  padding-left: 1.5em;
}

.copilot-markdown :deep(ul) {
  list-style-type: disc;
}

.copilot-markdown :deep(ol) {
  list-style-type: decimal;
}

.copilot-markdown :deep(li) {
  margin: 0.35em 0;
}

.copilot-markdown :deep(.copilot-md-table) {
  width: 100%;
  border-collapse: collapse;
  margin: 0.75em 0;
  font-size: 14px;
  border-radius: var(--radius-card);
  overflow: hidden;
  border: 1px solid #e5e7eb;
}

.copilot-markdown :deep(.copilot-md-table thead) {
  background: #f9fafb;
}

.copilot-markdown :deep(.copilot-md-table th),
.copilot-markdown :deep(.copilot-md-table td) {
  padding: 10px 12px;
  text-align: left;
  border-bottom: 1px solid #e5e7eb;
  vertical-align: top;
}

.copilot-markdown :deep(.copilot-md-table th) {
  font-weight: 600;
  color: #374151;
}

.copilot-markdown :deep(.copilot-md-table tbody tr:last-child td) {
  border-bottom: none;
}

.copilot-markdown :deep(.copilot-md-table tbody tr:hover) {
  background: #f8fafc;
}

.copilot-markdown :deep(.copilot-md-hr) {
  border: none;
  border-top: 1px solid #e5e7eb;
  margin: 1em 0;
}
</style>

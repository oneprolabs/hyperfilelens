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
    .replace(/'/g, '&#39;')
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

function formatTextLines(text: string): string {
  return inlineFormat(escapeHtml(text).replace(/\n/g, '<br>'))
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
  if (lines.length < 2) return `<p>${formatTextLines(block)}</p>`

  const separator = lines[1].replace(/\|/g, '').trim()
  if (!separator.match(/^[\s:-]+$/)) {
    return `<p>${formatTextLines(block)}</p>`
  }

  const headers = parseTableCells(lines[0])
  const rows = lines.slice(2).map(parseTableCells)

  const thead = `<thead><tr>${headers.map((h) => `<th>${inlineFormat(escapeHtml(h))}</th>`).join('')}</tr></thead>`
  const tbody = `<tbody>${rows.map((row) => `<tr>${row.map((cell) => `<td>${inlineFormat(escapeHtml(cell))}</td>`).join('')}</tr>`).join('')}</tbody>`
  return `<div class="copilot-md-table-wrap"><table class="copilot-md-table">${thead}${tbody}</table></div>`
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

  if (lines.every((line) => /^>\s?/.test(line))) {
    const quote = lines.map((line) => line.replace(/^>\s?/, '')).join('\n')
    return `<blockquote class="copilot-md-quote">${inlineFormat(escapeHtml(quote).replace(/\n/g, '<br>'))}</blockquote>`
  }

  return `<p>${formatTextLines(block)}</p>`
}

function normalizeCodeLanguage(value: string): string {
  return value.trim().replace(/[^a-zA-Z0-9_+#.-]/g, '').slice(0, 24)
}

function renderMarkdown(text: string): string {
  if (!text.trim()) return ''
  const normalized = text.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const fenceCount = normalized.match(/^```/gm)?.length ?? 0
  const renderable = fenceCount % 2 === 1 ? `${normalized}\n\`\`\`` : normalized

  const codeBlocks: string[] = []
  const working = renderable.replace(/```([^\n`]*)\n([\s\S]*?)```/g, (_match, rawLang, code) => {
    const index = codeBlocks.length
    const language = normalizeCodeLanguage(rawLang)
    const languageLabel = language
      ? `<span class="copilot-md-code-language">${escapeHtml(language)}</span>`
      : ''
    codeBlocks.push(
      `<div class="copilot-md-code-block">${languageLabel}<pre class="copilot-md-pre"><code>${escapeHtml(code.replace(/^\n|\n$/g, ''))}</code></pre></div>`,
    )
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
  overflow-wrap: anywhere;
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
  font-weight: 500;
  text-decoration: underline;
  text-decoration-thickness: 1px;
  text-underline-offset: 2px;
}

.copilot-markdown :deep(strong),
.copilot-markdown :deep(b) {
  color: var(--color-text-title);
  font-weight: 600;
}

.copilot-markdown :deep(.copilot-md-code) {
  padding: 0.08em 0.32em;
  border: 1px solid color-mix(in srgb, var(--color-primary) 12%, var(--color-border));
  border-radius: 4px;
  background: color-mix(in srgb, var(--color-primary) 5%, var(--color-grey-1));
  color: var(--color-text-primary);
  font-family: var(--font-mono);
  font-size: 0.9em;
  font-weight: 500;
}

.copilot-markdown :deep(.copilot-md-code-block) {
  position: relative;
  margin: 16px 0;
  overflow: hidden;
  border: 1px solid #263244;
  border-radius: 12px;
  background: #111827;
}

.copilot-markdown :deep(.copilot-md-code-language) {
  display: block;
  padding: 8px 14px 0;
  color: #94a3b8;
  font-family: var(--font-mono);
  font-size: 11px;
  font-weight: 600;
  line-height: 1.4;
  text-transform: lowercase;
}

.copilot-markdown :deep(.copilot-md-pre) {
  margin: 0;
  padding: 14px 16px 16px;
  overflow-x: auto;
  border-radius: 0;
  background: transparent;
  color: #e2e8f0;
  font-family: var(--font-mono);
  font-size: 13px;
  line-height: 1.65;
  tab-size: 2;
}

.copilot-markdown :deep(.copilot-md-pre code) {
  display: block;
  background: transparent;
  padding: 0;
  color: inherit;
  font: inherit;
  white-space: pre;
}

.copilot-markdown :deep(h1),
.copilot-markdown :deep(h2),
.copilot-markdown :deep(h3),
.copilot-markdown :deep(h4),
.copilot-markdown :deep(h5),
.copilot-markdown :deep(h6) {
  margin: 1em 0 0.5em;
  font-weight: 650;
  line-height: 1.35;
  color: var(--color-text-title);
}

.copilot-markdown :deep(> h1:first-child),
.copilot-markdown :deep(> h2:first-child),
.copilot-markdown :deep(> h3:first-child),
.copilot-markdown :deep(> h4:first-child),
.copilot-markdown :deep(> h5:first-child),
.copilot-markdown :deep(> h6:first-child) {
  margin-top: 0;
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

.copilot-markdown :deep(li::marker) {
  color: var(--color-text-secondary);
}

.copilot-markdown :deep(.copilot-md-table-wrap) {
  width: 100%;
  margin: 16px 0;
  overflow-x: auto;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  overscroll-behavior-inline: contain;
}

.copilot-markdown :deep(.copilot-md-table) {
  width: 100%;
  min-width: 520px;
  border-collapse: collapse;
  margin: 0;
  color: var(--color-text-primary);
  font-size: 13px;
  line-height: 1.5;
}

.copilot-markdown :deep(.copilot-md-table thead) {
  background: var(--color-grey-1);
}

.copilot-markdown :deep(.copilot-md-table th),
.copilot-markdown :deep(.copilot-md-table td) {
  padding: 9px 12px;
  text-align: left;
  border-bottom: 1px solid var(--color-border);
  border-right: 1px solid var(--color-border);
  vertical-align: top;
}

.copilot-markdown :deep(.copilot-md-table th:last-child),
.copilot-markdown :deep(.copilot-md-table td:last-child) {
  border-right: none;
}

.copilot-markdown :deep(.copilot-md-table th) {
  font-weight: 650;
  color: var(--color-text-title);
}

.copilot-markdown :deep(.copilot-md-table tbody tr:last-child td) {
  border-bottom: none;
}

.copilot-markdown :deep(.copilot-md-table tbody tr:hover) {
  background: var(--color-grey-1);
}

.copilot-markdown :deep(.copilot-md-hr) {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 1em 0;
}

.copilot-markdown :deep(.copilot-md-quote) {
  margin: 14px 0;
  padding: 2px 0 2px 14px;
  border-left: 3px solid color-mix(in srgb, var(--color-primary) 42%, var(--color-border));
  color: var(--color-text-secondary);
}

@media (max-width: 768px) {
  .copilot-markdown {
    font-size: 16px;
    line-height: 1.65;
  }

  .copilot-markdown :deep(.copilot-md-code-block),
  .copilot-markdown :deep(.copilot-md-table-wrap) {
    margin: 14px 0;
  }

  .copilot-markdown :deep(.copilot-md-pre) {
    font-size: 12px;
  }
}
</style>

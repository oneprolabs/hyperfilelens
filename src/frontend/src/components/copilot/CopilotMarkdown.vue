<script setup lang="ts">
import { computed } from 'vue'
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/core'
import javascript from 'highlight.js/lib/languages/javascript'
import python from 'highlight.js/lib/languages/python'
import bash from 'highlight.js/lib/languages/bash'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import { marked, type Tokens } from 'marked'
import { useI18n } from 'vue-i18n'
import { copyTextToClipboard } from '../../lib/clipboard'

const props = defineProps<{ content: string }>()
const { t, locale } = useI18n()
const SAFE_URI_PATTERN = /^(?:(?:https?|mailto):|(?:\/|\.\.?\/|#))/i
const copyResetTimers = new WeakMap<HTMLButtonElement, ReturnType<typeof setTimeout>>()
const mindmapPointers = new WeakMap<HTMLElement, Map<number, { x: number; y: number }>>()

hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('js', javascript)
hljs.registerLanguage('typescript', javascript)
hljs.registerLanguage('ts', javascript)
hljs.registerLanguage('python', python)
hljs.registerLanguage('py', python)
hljs.registerLanguage('bash', bash)
hljs.registerLanguage('sh', bash)
hljs.registerLanguage('shell', bash)
hljs.registerLanguage('json', json)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)

function escapeHtml(value: string): string {
  return value.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;')
}

function safeUri(value: string | null | undefined): string {
  const uri = (value || '').trim()
  return SAFE_URI_PATTERN.test(uri) ? uri : ''
}

function codeLanguage(value: string | undefined): string {
  return (value || '').trim().replace(/[^a-zA-Z0-9_+#.-]/g, '').slice(0, 32)
}

function renderMindmap(source: string): string {
  type MindmapNode = {
    label: string; depth: number; path: string; parent: string
    children: MindmapNode[]; lines: string[]; textWidth: number; labelX?: number
    labelEnd?: number; dotX?: number; y?: number; hasChildren?: boolean
  }
  const cleanLabel = (value: string) => value.trim().replace(/^(\*\*|__)([\s\S]+)\1$/, '$2')
  const root: MindmapNode = { label: 'Mindmap', depth: 0, path: '0', parent: '', children: [], lines: [], textWidth: 0 }
  const stack: Array<{ node: MindmapNode; indent: number }> = [{ node: root, indent: -1 }]
  let heading = root
  for (const line of source.replace(/\r/g, '').split('\n')) {
    const headingMatch = line.match(/^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$/)
    const bulletMatch = line.match(/^(\s*)(?:[-*+]|\d+\.)\s+(.+?)\s*$/)
    if (headingMatch) {
      const node: MindmapNode = { label: cleanLabel(headingMatch[1]!), depth: 1, path: `0.${root.children.length + 1}`, parent: '0', children: [], lines: [], textWidth: 0 }
      root.children.push(node); heading = node
      stack.splice(0, stack.length, { node: root, indent: -1 }, { node, indent: -1 })
    } else if (bulletMatch) {
      const indent = bulletMatch[1]!.replace(/\t/g, '  ').length
      while (stack.length > 2 && indent <= stack[stack.length - 1]!.indent) stack.pop()
      const parent = stack[stack.length - 1]!.node || heading
      const node: MindmapNode = { label: cleanLabel(bulletMatch[2]!), depth: parent.depth + 1, path: `${parent.path}.${parent.children.length + 1}`, parent: parent.path, children: [], lines: [], textWidth: 0 }
      parent.children.push(node); stack.push({ node, indent })
    }
  }
  if (!root.children.length && source.trim()) root.label = cleanLabel(source.trim().split('\n')[0]!)
  const all: MindmapNode[] = []
  const visit = (node: MindmapNode) => { all.push(node); node.children.forEach(visit) }
  visit(root)
  const measure = (value: string) => [...value].reduce((sum, char) => sum + (char.codePointAt(0)! > 0x2e7f ? 14 : 7.5), 0)
  const wrap = (value: string, max = 240) => {
    const lines: string[] = []; let current = ''; let width = 0
    for (const char of [...value]) { const charWidth = measure(char); if (current && width + charWidth > max) { lines.push(current); current = ''; width = 0 } current += char; width += charWidth }
    if (current || !lines.length) lines.push(current)
    return lines
  }
  const maxDepth = Math.max(...all.map((node) => node.depth), 0)
  const columns: number[] = []
  for (const node of all) { node.lines = node.depth ? wrap(node.label) : []; node.textWidth = Math.max(0, ...node.lines.map(measure)); columns[node.depth] = Math.max(columns[node.depth] || 0, node.textWidth) }
  const labelX: number[] = [112]
  for (let depth = 1; depth <= maxDepth; depth++) labelX[depth] = depth === 1 ? labelX[0]! + 104 : labelX[depth - 1]! + (columns[depth - 1] || 0) + 12 + 40
  let nextY = 24
  const place = (node: MindmapNode) => {
    node.labelX = labelX[node.depth]; node.labelEnd = node.labelX! + node.textWidth; node.hasChildren = node.children.length > 0; node.dotX = node.depth ? node.labelEnd + 12 : labelX[0]
    if (!node.children.length) { node.y = nextY; nextY += Math.max(34, Math.max(1, node.lines.length) * 20 + 12); return }
    node.children.forEach(place); node.y = (node.children[0]!.y! + node.children[node.children.length - 1]!.y!) / 2
  }
  place(root)
  const width = Math.max(560, (labelX[maxDepth] || 112) + (columns[maxDepth] || 0) + 36)
  const height = Math.max(150, nextY + 8)
  const colors = ['#0057ff', '#27ce6e', '#ff9500', '#f94d4d']
  const branchColor = (node: MindmapNode) => node.depth ? colors[(Number(node.path.split('.')[1] || 1) - 1) % colors.length]! : '#0057ff'
  const text = (node: MindmapNode) => node.lines.map((line, index) => `<tspan x="${node.labelX}" dy="${index ? 20 : -((node.lines.length - 1) * 20) / 2}">${escapeHtml(line)}</tspan>`).join('')
  const links = all.filter((node) => node.depth > 0).map((node) => { const parent = all.find((candidate) => candidate.path === node.parent); if (!parent) return ''; const start = parent.dotX!; const end = node.labelX! - 10; const middle = (start + end) / 2; return `<path class="copilot-md-mindmap-link" data-mindmap-link="${node.path}" d="M${start},${parent.y} C${middle},${parent.y} ${middle},${node.y} ${end},${node.y}" stroke="${branchColor(node)}"/>` }).join('')
  const nodes = all.map((node) => `<g class="copilot-md-mindmap-node" data-mindmap-toggle="true" data-mindmap-path="${node.path}" data-mindmap-parent="${node.parent}" data-mindmap-has-children="${node.hasChildren}" data-mindmap-collapsed="false" role="treeitem" tabindex="0" aria-expanded="${node.hasChildren}" aria-label="${escapeHtml(node.label)}">${node.hasChildren ? `<circle cx="${node.dotX}" cy="${node.y}" r="${node.depth ? 6 : 7}" stroke="${branchColor(node)}" fill="white"/>` : ''}<text x="${node.labelX}" y="${node.y}" fill="currentColor" dominant-baseline="middle">${text(node)}</text></g>`).join('')
  return `<div class="copilot-md-mindmap-wrap" data-mindmap-root><div class="copilot-md-mindmap-canvas" role="group" aria-label="${escapeHtml(root.label)}"><svg class="copilot-md-mindmap" role="tree" viewBox="0 0 ${width} ${height}" data-mindmap-width="${width}" data-mindmap-height="${height}" preserveAspectRatio="xMinYMin meet" aria-label="${escapeHtml(root.label)}"><g class="copilot-md-mindmap-links">${links}</g><g class="copilot-md-mindmap-nodes">${nodes}</g></svg><pre class="copilot-md-mindmap-code" aria-hidden="true"><code>${escapeHtml(source)}</code></pre></div><div class="copilot-md-mindmap-toolbar" role="toolbar" aria-label="${escapeHtml(t('insight.copilot.mindmapControls'))}"><button type="button" data-mindmap-action="zoom-out" aria-label="${escapeHtml(t('insight.copilot.mindmapZoomOut'))}" title="${escapeHtml(t('insight.copilot.mindmapZoomOut'))}">−</button><button type="button" data-mindmap-action="zoom-in" aria-label="${escapeHtml(t('insight.copilot.mindmapZoomIn'))}" title="${escapeHtml(t('insight.copilot.mindmapZoomIn'))}">+</button><button type="button" data-mindmap-action="fit" aria-label="${escapeHtml(t('insight.copilot.mindmapFit'))}" title="${escapeHtml(t('insight.copilot.mindmapFit'))}">${escapeHtml(t('insight.copilot.mindmapFit'))}</button><button type="button" data-mindmap-action="fullscreen" aria-label="${escapeHtml(t('insight.copilot.mindmapFullscreen'))}" title="${escapeHtml(t('insight.copilot.mindmapFullscreen'))}">□</button><button type="button" data-mindmap-action="code" aria-label="${escapeHtml(t('insight.copilot.mindmapSource'))}" title="${escapeHtml(t('insight.copilot.mindmapSource'))}">&lt;/&gt;</button></div></div>`
}

const renderer = new marked.Renderer()
renderer.link = function ({ href, title, tokens }: Tokens.Link) {
  const safeHref = safeUri(href)
  const text = this.parser.parseInline(tokens)
  if (!safeHref) return text
  const titleAttribute = title ? ` title="${escapeHtml(title)}"` : ''
  return `<a href="${escapeHtml(safeHref)}"${titleAttribute} target="_blank" rel="noopener noreferrer">${text}</a>`
}
renderer.image = ({ href, title, text }: Tokens.Image) => {
  const safeSrc = safeUri(href)
  if (!safeSrc) return escapeHtml(text || '')
  const titleAttribute = title ? ` title="${escapeHtml(title)}"` : ''
  return `<img src="${escapeHtml(safeSrc)}" alt="${escapeHtml(text || '')}"${titleAttribute} loading="lazy" decoding="async">`
}
renderer.code = ({ text, lang }: Tokens.Code) => {
  const language = codeLanguage(lang)
  if (language.toLowerCase() === 'mindmap') return renderMindmap(text)
  let highlighted = escapeHtml(text)
  if (language && hljs.getLanguage(language)) {
    highlighted = hljs.highlight(text, { language, ignoreIllegals: true }).value
  }
  const languageLabel = language
    ? `<span class="copilot-md-code-language">${escapeHtml(language)}</span>`
    : ''
  const className = language ? ` class="hljs language-${escapeHtml(language)}"` : ''
  const copyLabel = escapeHtml(t('common.copy'))
  return `<div class="copilot-md-code-block"><div class="copilot-md-code-header">${languageLabel}<button type="button" class="copilot-md-code-copy" aria-label="${copyLabel}" title="${copyLabel}"></button></div><pre class="copilot-md-pre"><code${className}>${highlighted}</code></pre></div>`
}

renderer.table = function ({ header, rows, align }: Tokens.Table) {
  const renderCell = (cell: Tokens.TableCell, index: number) => {
    const tag = cell.header ? 'th' : 'td'
    const alignment = align[index] ? ` align="${align[index]}"` : ''
    return `<${tag}${alignment}>${this.parser.parseInline(cell.tokens)}</${tag}>`
  }
  const headerHtml = header.length
    ? `<thead><tr>${header.map(renderCell).join('')}</tr></thead>`
    : ''
  const bodyHtml = rows.length
    ? `<tbody>${rows.map((row) => `<tr>${row.map(renderCell).join('')}</tr>`).join('')}</tbody>`
    : ''
  return `<div class="copilot-md-table-wrap"><table class="copilot-md-table">${headerHtml}${bodyHtml}</table></div>`
}

// Preserve SourceLens-compatible HTML output. DOMPurify below removes scripts,
// event handlers, unsafe URLs, and other dangerous markup before it reaches the DOM.
renderer.html = ({ raw }: Tokens.HTML) => raw

marked.use({
  renderer,
  breaks: true,
  gfm: true,
  hooks: {
    postprocess(html) {
      return DOMPurify.sanitize(html, {
        ALLOWED_TAGS: [
          'p', 'br', 'strong', 'em', 'u', 's', 'del', 'code', 'pre',
          'blockquote', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol',
          'li', 'hr', 'a', 'img', 'table', 'thead', 'tbody', 'tr', 'th',
          'td', 'span', 'div', 'input', 'button', 'svg', 'g', 'path', 'rect',
          'circle', 'text', 'tspan',
        ],
        ALLOWED_ATTR: [
          'href', 'src', 'alt', 'title', 'class', 'target', 'rel', 'type',
          'checked', 'disabled', 'align', 'loading', 'decoding', 'role',
          'aria-label', 'aria-expanded', 'aria-hidden', 'tabindex', 'viewBox',
          'preserveAspectRatio', 'x', 'y', 'width', 'height', 'rx', 'cx', 'cy',
          'r', 'd', 'dy', 'fill', 'stroke', 'font-size', 'text-anchor',
          'dominant-baseline',
        ],
        ALLOW_DATA_ATTR: false,
        ADD_ATTR: [
          'data-mindmap-root', 'data-mindmap-action', 'data-mindmap-toggle',
          'data-mindmap-path', 'data-mindmap-parent', 'data-mindmap-has-children',
          'data-mindmap-collapsed', 'data-collapsed', 'data-mindmap-width',
          'data-mindmap-height', 'data-mindmap-link', 'data-zoom', 'data-copied',
        ],
      })
    },
  },
})

function renderMarkdown(value: string): string {
  if (!value.trim()) return ''
  const normalized = value.replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const fenceCount = normalized.match(/^\s*```/gm)?.length ?? 0
  const renderable = fenceCount % 2 === 1 ? `${normalized}\n\`\`\`` : normalized
  return marked.parse(renderable) as string
}

const rendered = computed(() => {
  // Keep localized mindmap controls in sync when the user changes language.
  void locale.value
  return renderMarkdown(props.content)
})

async function handleMarkdownInteraction(event: MouseEvent | KeyboardEvent) {
  const target = event.target
  if (!(target instanceof Element)) return
  const action = target.closest<HTMLElement>('[data-mindmap-action]')
  const root = target.closest<HTMLElement>('[data-mindmap-root]')
  if (action && root) {
    const canvas = root.querySelector<HTMLElement>('.copilot-md-mindmap-canvas')
    const svg = root.querySelector<SVGElement>('.copilot-md-mindmap')
    if (!canvas || !svg) return
    const name = action.dataset.mindmapAction
    const currentZoom = Number(svg.dataset.zoom || '1')
    if (name === 'zoom-in' || name === 'zoom-out') {
      const zoom = Math.max(0.5, Math.min(2, currentZoom + (name === 'zoom-in' ? 0.15 : -0.15)))
      svg.dataset.zoom = String(zoom)
      const baseWidth = Number(svg.dataset.mindmapWidth || '560')
      const baseHeight = Number(svg.dataset.mindmapHeight || '180')
      svg.style.width = `${Math.max(canvas.clientWidth || baseWidth, baseWidth * zoom)}px`
      svg.style.height = `${Math.max(180, baseHeight * zoom)}px`
    } else if (name === 'fit') {
      const baseWidth = Number(svg.dataset.mindmapWidth || '560')
      const viewportWidth = canvas.clientWidth || baseWidth
      const zoom = Math.max(0.5, Math.min(1, viewportWidth / baseWidth))
      svg.dataset.zoom = String(zoom)
      svg.style.width = `${Math.max(viewportWidth, baseWidth * zoom)}px`
      svg.style.height = `${Math.max(180, Number(svg.dataset.mindmapHeight || '180') * zoom)}px`
      canvas.scrollLeft = 0
      canvas.scrollTop = 0
    } else if (name === 'code') {
      root.classList.toggle('copilot-md-mindmap--code')
    } else if (name === 'fullscreen') {
      await root.requestFullscreen?.()
    }
    return
  }
  const node = target.closest<SVGGElement>('[data-mindmap-has-children="true"]')
  const nodeRoot = target.closest<HTMLElement>('[data-mindmap-root]')
  if (node && nodeRoot) {
    if (event instanceof KeyboardEvent) {
      if (event.key !== 'Enter' && event.key !== ' ') return
      event.preventDefault()
    }
    const path = node.dataset.mindmapPath || ''
    const collapsed = node.dataset.mindmapCollapsed === 'true'
    node.dataset.mindmapCollapsed = String(!collapsed)
    node.setAttribute('aria-expanded', String(collapsed))
    const isDescendant = (value: string, ancestor: string) => value === ancestor || value.startsWith(`${ancestor}.`)
    nodeRoot.querySelectorAll<SVGElement>('[data-mindmap-path], [data-mindmap-link]').forEach((element) => {
      const elementPath = element.dataset.mindmapPath || ''
      const elementLink = element.dataset.mindmapLink || ''
      if (elementPath === path) return
      const hiddenByCollapsedAncestor = Array.from(nodeRoot.querySelectorAll<SVGElement>('[data-mindmap-has-children="true"][data-mindmap-collapsed="true"]')).some((collapsedNode) => {
        const collapsedPath = collapsedNode.dataset.mindmapPath || ''
        return isDescendant(elementPath || elementLink, collapsedPath)
      })
      element.style.display = hiddenByCollapsedAncestor ? 'none' : ''
    })
    return
  }
  const button = target.closest<HTMLButtonElement>('.copilot-md-code-copy')
  if (!button) return
  const code = button.closest('.copilot-md-code-block')?.querySelector('code')
  if (!code) return
  try {
    await copyTextToClipboard(code.textContent || '')
  } catch {
    return
  }
  const copiedLabel = t('common.copied')
  button.title = copiedLabel
  button.setAttribute('aria-label', copiedLabel)
  button.dataset.copied = 'true'
  const existingTimer = copyResetTimers.get(button)
  if (existingTimer) clearTimeout(existingTimer)
  copyResetTimers.set(button, setTimeout(() => {
    const copyLabel = t('common.copy')
    button.title = copyLabel
    button.setAttribute('aria-label', copyLabel)
    delete button.dataset.copied
    copyResetTimers.delete(button)
  }, 1800))
}

function mindmapCanvas(event: PointerEvent | WheelEvent): HTMLElement | null {
  const target = event.target
  return target instanceof Element ? target.closest<HTMLElement>('.copilot-md-mindmap-canvas') : null
}

function handleMindmapPointerDown(event: PointerEvent) {
  const canvas = mindmapCanvas(event)
  if (!canvas || (event.target instanceof Element && event.target.closest('button, [data-mindmap-toggle]'))) return
  const pointers = mindmapPointers.get(canvas) || new Map<number, { x: number; y: number }>()
  pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
  mindmapPointers.set(canvas, pointers)
  canvas.dataset.dragging = 'true'
  canvas.dataset.dragX = String(event.clientX)
  canvas.dataset.dragY = String(event.clientY)
  canvas.dataset.scrollLeft = String(canvas.scrollLeft)
  canvas.dataset.scrollTop = String(canvas.scrollTop)
  canvas.setPointerCapture?.(event.pointerId)
}

function handleMindmapPointerMove(event: PointerEvent) {
  const canvas = mindmapCanvas(event)
  if (!canvas || canvas.dataset.dragging !== 'true') return
  const pointers = mindmapPointers.get(canvas)
  if (pointers?.has(event.pointerId)) pointers.set(event.pointerId, { x: event.clientX, y: event.clientY })
  canvas.scrollLeft = Number(canvas.dataset.scrollLeft || canvas.scrollLeft) - (event.clientX - Number(canvas.dataset.dragX || event.clientX))
  canvas.scrollTop = Number(canvas.dataset.scrollTop || canvas.scrollTop) - (event.clientY - Number(canvas.dataset.dragY || event.clientY))
}

function handleMindmapPointerUp(event: PointerEvent) {
  const canvas = mindmapCanvas(event)
  if (!canvas) return
  mindmapPointers.get(canvas)?.delete(event.pointerId)
  canvas.dataset.dragging = 'false'
  canvas.releasePointerCapture?.(event.pointerId)
}

function handleMindmapWheel(event: WheelEvent) {
  if (!event.ctrlKey && !event.metaKey) return
  const canvas = mindmapCanvas(event)
  if (!canvas) return
  event.preventDefault()
  const root = canvas.closest<HTMLElement>('[data-mindmap-root]')
  const svg = root?.querySelector<SVGElement>('.copilot-md-mindmap')
  if (!root || !svg) return
  const zoom = Number(svg.dataset.zoom || '1') + (event.deltaY < 0 ? 0.15 : -0.15)
  const baseWidth = Number(svg.dataset.mindmapWidth || '560')
  const baseHeight = Number(svg.dataset.mindmapHeight || '180')
  const next = Math.max(0.5, Math.min(2, zoom))
  svg.dataset.zoom = String(next)
  svg.style.width = `${Math.max(canvas.clientWidth || baseWidth, baseWidth * next)}px`
  svg.style.height = `${Math.max(180, baseHeight * next)}px`
}
</script>

<template>
  <!-- Markdown is rendered through Marked and sanitized with DOMPurify. -->
  <!-- eslint-disable vue/no-v-html -->
  <div
    class="copilot-markdown"
    @click="handleMarkdownInteraction"
    @keydown="handleMarkdownInteraction"
    @pointerdown="handleMindmapPointerDown"
    @pointermove="handleMindmapPointerMove"
    @pointerup="handleMindmapPointerUp"
    @pointercancel="handleMindmapPointerUp"
    @lostpointercapture="handleMindmapPointerUp"
    @wheel="handleMindmapWheel"
    v-html="rendered"
  />
  <!-- eslint-enable vue/no-v-html -->
</template>

<style scoped>
.copilot-markdown {
  font-family: var(--font-sans); font-size: 15px; line-height: 24px; font-weight: 400;
  letter-spacing: normal; color: #171512; overflow-wrap: anywhere;
  -webkit-font-smoothing: antialiased; -moz-osx-font-smoothing: grayscale;
}
.copilot-markdown :deep(p) { margin: 0 0 10px; color: #171512; }
.copilot-markdown :deep(p:last-child) { margin-bottom: 0; }
.copilot-markdown :deep(a) { color: #2563eb; font-weight: 400; text-decoration: underline; }
.copilot-markdown :deep(a:hover) { color: #1d4ed8; }
.copilot-markdown :deep(strong), .copilot-markdown :deep(b) { color: #171512; font-weight: 600; }
.copilot-markdown :deep(del) { color: #4b5563; }
.copilot-markdown :deep(.copilot-md-code), .copilot-markdown :deep(:not(pre) > code) { padding: 2px 4px; border: none; border-radius: 12px; background: #eef4fe; color: #0e278c; font-family: var(--font-mono); font-size: 14px; font-weight: 400; }
.copilot-markdown :deep(.copilot-md-code-block) { position: relative; margin: 16px 0; overflow: hidden; border: 1px solid #263244; border-radius: 12px; background: #111827; }
.copilot-markdown :deep(.copilot-md-code-header) { display: flex; align-items: center; justify-content: space-between; min-height: 32px; padding: 4px 8px 0 14px; }
.copilot-markdown :deep(.copilot-md-code-language) { display: block; padding: 4px 0 0; color: #94a3b8; font-family: var(--font-mono); font-size: 11px; font-weight: 600; line-height: 1.4; text-transform: lowercase; }
.copilot-markdown :deep(.copilot-md-code-copy) { width: 28px; height: 28px; border: 0; border-radius: 6px; background: transparent; color: #94a3b8; cursor: pointer; }
.copilot-markdown :deep(.copilot-md-code-copy::before) { content: '⧉'; font-size: 16px; line-height: 1; }
.copilot-markdown :deep(.copilot-md-code-copy[data-copied]::before) { content: '✓'; color: #86efac; }
.copilot-markdown :deep(.copilot-md-code-copy:hover) { background: rgb(255 255 255 / 10%); color: #fff; }
.copilot-markdown :deep(.copilot-md-code-copy:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 1px; }
.copilot-markdown :deep(.copilot-md-pre) { margin: 0; padding: 14px 16px 16px; overflow-x: auto; background: transparent; color: #e2e8f0; font-family: var(--font-mono); font-size: 13px; line-height: 1.65; tab-size: 2; }
.copilot-markdown :deep(.copilot-md-pre code) { display: block; padding: 0; background: transparent; color: inherit; font: inherit; white-space: pre; }
.copilot-markdown :deep(h1), .copilot-markdown :deep(h2), .copilot-markdown :deep(h3), .copilot-markdown :deep(h4), .copilot-markdown :deep(h5), .copilot-markdown :deep(h6) { margin: 1em 0 0.5em; color: var(--color-text-title); font-weight: 650; line-height: 1.35; }
.copilot-markdown :deep(> h1:first-child), .copilot-markdown :deep(> h2:first-child), .copilot-markdown :deep(> h3:first-child), .copilot-markdown :deep(> h4:first-child), .copilot-markdown :deep(> h5:first-child), .copilot-markdown :deep(> h6:first-child) { margin-top: 0; }
.copilot-markdown :deep(h1) { font-size: 1.5em; } .copilot-markdown :deep(h2) { font-size: 1.25em; } .copilot-markdown :deep(h3) { font-size: 1.1em; } .copilot-markdown :deep(h4) { font-size: 1em; } .copilot-markdown :deep(h5), .copilot-markdown :deep(h6) { font-size: 0.95em; }
.copilot-markdown :deep(ul), .copilot-markdown :deep(ol) { margin: 0 0 12px; padding-left: 20px; }
.copilot-markdown :deep(li) { margin: 0 0 6px; color: #171512; font-size: 15px; line-height: 24px; } .copilot-markdown :deep(li::marker) { color: #4b5563; }
.copilot-markdown :deep(input[type='checkbox']) { margin-right: 0.45em; accent-color: var(--color-primary); }
.copilot-markdown :deep(.copilot-md-table-wrap) { width: 100%; max-width: 100%; margin: 16px 0; overflow-x: auto; }
.copilot-markdown :deep(table) { width: 100%; margin: 0; border-collapse: collapse; border: 1px solid #d1d5db; font-size: 15px; line-height: 24px; }
.copilot-markdown :deep(th), .copilot-markdown :deep(td) { padding: 8px 12px; border: 1px solid #d1d5db; text-align: left; vertical-align: middle; }
.copilot-markdown :deep(th) { background: #f9fafb; color: #171512; font-weight: 500; }
.copilot-markdown :deep(td) { color: #4b5563; }
.copilot-markdown :deep(hr) { margin: 1em 0; border: 0; border-top: 1px solid var(--color-border); }
.copilot-markdown :deep(blockquote) { margin: 14px 0; padding: 2px 0 2px 14px; border-left: 3px solid color-mix(in srgb, var(--color-primary) 42%, var(--color-border)); color: var(--color-text-secondary); }
.copilot-markdown :deep(img) { display: block; max-width: 100%; height: auto; margin: 12px 0; border: 1px solid var(--color-border); border-radius: 8px; }
.copilot-markdown :deep(.copilot-md-mindmap-wrap) { width: 100%; margin: 16px 0; overflow: hidden; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-card-bg); }
.copilot-markdown :deep(.copilot-md-mindmap-canvas) { max-height: 560px; min-height: 180px; overflow: auto; padding: 12px; cursor: grab; touch-action: none; }
.copilot-markdown :deep(.copilot-md-mindmap-canvas[data-dragging='true']) { cursor: grabbing; }
.copilot-markdown :deep(.copilot-md-mindmap) { display: block; min-width: 560px; width: max(100%, 560px); min-height: 180px; transition: width 0.18s ease, height 0.18s ease; color: var(--color-text-primary); }
.copilot-markdown :deep(.copilot-md-mindmap-link) { fill: none; stroke: var(--color-primary); stroke-width: 1.5; opacity: 0.55; }
.copilot-markdown :deep(.copilot-md-mindmap-node) { cursor: pointer; outline: none; }
.copilot-markdown :deep(.copilot-md-mindmap-node circle) { stroke-width: 1.5; }
.copilot-markdown :deep(.copilot-md-mindmap-node text) { fill: currentColor; font: 400 14px/18px sans-serif; }
.copilot-markdown :deep(.copilot-md-mindmap-node:hover text), .copilot-markdown :deep(.copilot-md-mindmap-node:focus text) { font-weight: 600; }
.copilot-markdown :deep(.copilot-md-mindmap-node:focus circle) { stroke-width: 2.5; }
.copilot-markdown :deep(.copilot-md-mindmap-node-hidden), .copilot-markdown :deep(.copilot-md-mindmap-link-hidden) { display: none; }
.copilot-markdown :deep(.copilot-md-mindmap-code) { display: none; margin: 0; padding: 14px; white-space: pre-wrap; }
.copilot-markdown :deep(.copilot-md-mindmap--code .copilot-md-mindmap) { display: none; }
.copilot-markdown :deep(.copilot-md-mindmap--code .copilot-md-mindmap-code) { display: block; }
.copilot-markdown :deep(.copilot-md-mindmap-toolbar) { display: flex; gap: 6px; align-items: center; padding: 8px 10px; border-top: 1px solid var(--color-border); }
.copilot-markdown :deep(.copilot-md-mindmap-toolbar button) { min-width: 40px; min-height: 40px; padding: 6px 10px; border: 1px solid var(--color-border); border-radius: 6px; background: var(--color-card-bg); color: var(--color-text-secondary); cursor: pointer; font: inherit; font-size: 12px; }
.copilot-markdown :deep(.copilot-md-mindmap-toolbar button:hover) { border-color: var(--color-primary); color: var(--color-primary); }
.copilot-markdown :deep(.copilot-md-mindmap-toolbar button:focus-visible), .copilot-markdown :deep(.copilot-md-mindmap-node:focus-visible) { outline: 2px solid var(--color-primary); outline-offset: 2px; }
.copilot-markdown :deep(.copilot-md-mindmap-wrap:fullscreen) { display: flex; width: 100vw; height: 100vh; margin: 0; flex-direction: column; border-radius: 0; }
.copilot-markdown :deep(.copilot-md-mindmap-wrap:fullscreen .copilot-md-mindmap-canvas) { max-height: none; flex: 1 1 auto; }
@media (max-width: 768px) { .copilot-markdown { font-size: 16px; line-height: 1.65; } .copilot-markdown :deep(.copilot-md-code-block), .copilot-markdown :deep(table) { margin: 14px 0; } .copilot-markdown :deep(.copilot-md-pre) { font-size: 12px; } }
</style>

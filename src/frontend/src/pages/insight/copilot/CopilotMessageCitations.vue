<script setup lang="ts">
import { BookOpen, Code2, ExternalLink, X } from 'lucide-vue-next'
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { fetchCopilotCitation, type LensCitation } from '../../../lib/lensApi'

const props = defineProps<{
  sessionId: number
  runId?: string
  citations: LensCitation[]
}>()
const { t } = useI18n()
const open = ref(false)
const loading = ref(false)
const error = ref('')
const drawerRef = ref<HTMLElement | null>(null)
const selected = ref<(LensCitation & { lines?: Array<{ number: number; content: string }> }) | null>(null)
let previousOverflow = ''

function location(citation: LensCitation) {
  return [citation.repository || citation.project, citation.path].filter(Boolean).join(' / ')
    || citation.path || citation.id
}

async function openCitation(citation: LensCitation) {
  if (!props.runId) return
  open.value = true
  selected.value = citation
  loading.value = true
  error.value = ''
  try {
    selected.value = await fetchCopilotCitation(props.sessionId, props.runId, citation.id)
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : t('insight.copilot.citationUnavailable')
    ElMessage.error({ message: error.value, grouping: true })
  } finally {
    loading.value = false
  }
}

function closeCitation() {
  open.value = false
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape' && open.value) closeCitation()
}

watch(open, async (visible) => {
  if (visible) {
    previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', handleKeydown)
    await nextTick()
    drawerRef.value?.focus()
  } else {
    document.body.style.overflow = previousOverflow
    document.removeEventListener('keydown', handleKeydown)
  }
})

onBeforeUnmount(() => {
  document.body.style.overflow = previousOverflow
  document.removeEventListener('keydown', handleKeydown)
})

function lineIsHighlighted(line: { number: number }) {
  if (!selected.value) return false
  const start = selected.value.highlight_start_line ?? selected.value.start_line
  const end = selected.value.highlight_end_line ?? selected.value.end_line ?? start
  return typeof start === 'number' && typeof end === 'number'
    && line.number >= start && line.number <= end
}

function lineRange(citation: LensCitation) {
  const start = citation.highlight_start_line ?? citation.start_line
  const end = citation.highlight_end_line ?? citation.end_line ?? start
  if (typeof start !== 'number') return ''
  return typeof end === 'number' && end !== start ? `${start}–${end}` : String(start)
}
</script>

<template>
  <details v-if="citations.length" class="message-citations">
    <summary class="message-citations-summary">
      <BookOpen :size="16" aria-hidden="true" />
      <span>{{ t('insight.copilot.citations') }}</span>
      <span class="message-citations-count">{{ citations.length }}</span>
    </summary>
    <div class="message-citations-list">
      <button
        v-for="citation in citations"
        :key="citation.id"
        type="button"
        class="message-citation"
        @click="openCitation(citation)"
      >
        <Code2 :size="17" class="message-citation-icon" aria-hidden="true" />
        <span class="message-citation-body">
          <span class="message-citation-location">{{ location(citation) }}</span>
          <span v-if="citation.symbol" class="message-citation-symbol">{{ citation.symbol }}</span>
          <span v-if="citation.supports" class="message-citation-supports">{{ citation.supports }}</span>
        </span>
        <ExternalLink :size="15" aria-hidden="true" />
      </button>
    </div>
  </details>

  <div v-if="open" class="citation-backdrop" role="presentation" @click.self="closeCitation">
    <aside
      ref="drawerRef"
      class="citation-drawer"
      role="dialog"
      aria-modal="true"
      tabindex="-1"
      :aria-label="t('insight.copilot.citationDetails')"
    >
      <header class="citation-drawer-header">
        <div>
          <h3>{{ t('insight.copilot.citationDetails') }}</h3>
          <p>{{ selected ? location(selected) : '' }}</p>
        </div>
        <button type="button" class="citation-close" :aria-label="t('common.close')" @click="closeCitation"><X :size="18" /></button>
      </header>
      <div v-if="loading" class="citation-state">{{ t('insight.copilot.citationLoading') }}</div>
      <div v-else-if="error" class="citation-state citation-state-error">
        <p>{{ error }}</p>
        <button
          v-if="selected"
          type="button"
          class="citation-retry"
          @click="openCitation(selected)"
        >
          {{ t('insight.copilot.citationRetry') }}
        </button>
      </div>
      <div v-else-if="selected" class="citation-content">
        <dl class="citation-meta">
          <div v-if="selected.revision"><dt>{{ t('insight.copilot.citationRevision') }}</dt><dd>{{ selected.revision }}</dd></div>
          <div v-if="selected.symbol"><dt>{{ t('insight.copilot.citationSymbol') }}</dt><dd>{{ selected.symbol }}</dd></div>
          <div v-if="lineRange(selected)"><dt>{{ t('insight.copilot.citationLines') }}</dt><dd>{{ lineRange(selected) }}</dd></div>
        </dl>
        <p v-if="selected.supports" class="citation-supports">{{ selected.supports }}</p>
        <pre v-if="selected.lines?.length" class="citation-code"><code><span v-for="line in selected.lines" :key="line.number" class="citation-code-line" :class="{ 'citation-code-line--highlighted': lineIsHighlighted(line) }"><span class="citation-line-number">{{ line.number }}</span>{{ line.content || ' ' }}\n</span></code></pre>
      </div>
    </aside>
  </div>
</template>

<style scoped>
.message-citations { margin-top: 16px; border-top: 1px solid var(--color-border); padding-top: 12px; }
.message-citations-summary { display: flex; align-items: center; gap: 8px; cursor: pointer; color: var(--color-text-secondary); font-size: 13px; font-weight: 600; }
.message-citations-count { color: var(--color-text-tertiary); font-weight: 400; }
.message-citations-list { display: grid; gap: 8px; margin-top: 10px; }
.message-citation { display: flex; align-items: flex-start; gap: 10px; width: 100%; padding: 10px 12px; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-card-bg); color: var(--color-text-secondary); text-align: left; cursor: pointer; }
.message-citation:hover { border-color: var(--color-primary); background: var(--color-grey-1); }
.message-citation-icon { flex: 0 0 auto; color: var(--color-primary); }
.message-citation-body { min-width: 0; flex: 1; }
.message-citation-location { display: block; overflow-wrap: anywhere; font-family: var(--font-mono); font-size: 12px; font-weight: 600; }
.message-citation-symbol, .message-citation-supports { display: block; margin-top: 3px; font-size: 12px; }
.citation-backdrop { position: fixed; inset: 0; z-index: 50; display: flex; justify-content: flex-end; background: rgb(15 23 42 / 45%); }
.citation-drawer { display: flex; width: min(720px, 100%); height: 100%; flex-direction: column; overflow: hidden; border-left: 1px solid var(--color-border); background: var(--color-card-bg); box-shadow: var(--shadow-lg); outline: none; animation: citation-drawer-in 0.18s ease-out; }
.citation-drawer-header { display: flex; flex: 0 0 auto; justify-content: space-between; gap: 16px; padding: 18px 20px; border-bottom: 1px solid var(--color-border); }
.citation-drawer-header h3 { margin: 0; color: var(--color-text-title); font-size: 16px; }
.citation-drawer-header p { margin: 4px 0 0; color: var(--color-text-secondary); font-family: var(--font-mono); font-size: 12px; overflow-wrap: anywhere; }
.citation-close { display: grid; place-items: center; width: 32px; height: 32px; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.citation-close:hover { background: var(--color-grey-1); }
.citation-content, .citation-state { min-height: 0; flex: 1 1 auto; overflow: auto; padding: 20px; }
.citation-state { color: var(--color-text-secondary); }
.citation-state-error { color: var(--color-danger, #dc2626); }
.citation-state-error p { margin: 0 0 12px; }
.citation-retry { min-height: 32px; padding: 6px 12px; border: 1px solid var(--color-primary); border-radius: 6px; background: transparent; color: var(--color-primary); cursor: pointer; font: inherit; font-size: 13px; font-weight: 600; }
.citation-retry:hover { background: color-mix(in srgb, var(--color-primary) 8%, transparent); }
.citation-meta { display: flex; flex-wrap: wrap; gap: 16px 28px; margin: 0 0 16px; }
.citation-meta dt { color: var(--color-text-secondary); font-size: 12px; }
.citation-meta dd { margin: 3px 0 0; color: var(--color-text-title); font-family: var(--font-mono); font-size: 12px; }
.citation-supports { color: var(--color-text-secondary); font-size: 13px; }
.citation-code { max-height: 55vh; overflow: auto; margin: 16px 0 0; padding: 12px 0; border: 1px solid var(--color-border); border-radius: 8px; background: #111827; color: #e2e8f0; font-size: 12px; line-height: 1.6; }
.citation-code-line { display: block; min-width: max-content; padding-right: 16px; white-space: pre; }
.citation-code-line--highlighted { background: color-mix(in srgb, var(--color-primary) 20%, transparent); }
.citation-line-number { display: inline-block; width: 56px; margin-right: 14px; color: #94a3b8; text-align: right; user-select: none; }
@keyframes citation-drawer-in { from { transform: translateX(12px); opacity: 0.6; } to { transform: translateX(0); opacity: 1; } }
@media (prefers-reduced-motion: reduce) { .citation-drawer { animation: none; } }
@media (max-width: 640px) { .citation-drawer { width: 100%; } .citation-drawer-header, .citation-content, .citation-state { padding: 16px; } }
</style>

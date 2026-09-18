<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronLeft, ChevronRight, Download, Maximize2, Minimize2, X } from 'lucide-vue-next'
import CopilotMarkdown from '../../../components/copilot/CopilotMarkdown.vue'
import { fetchCopilotAttachmentBlob, type LensRunOutputFile } from '../../../lib/lensApi'

const PREVIEW_MAX_BYTES = 5 * 1024 * 1024
const props = defineProps<{ sessionId: number; file: LensRunOutputFile | null }>()
const emit = defineEmits<{ close: []; download: [file: LensRunOutputFile] }>()
const { t } = useI18n()
const kind = ref('')
const loading = ref(false)
const failed = ref(false)
const fullscreen = ref(false)
const objectUrl = ref('')
const textContent = ref('')
const workbook = ref<{ XLSX: typeof import('xlsx'); sheets: Record<string, unknown> } | null>(null)
const sheetNames = ref<string[]>([])
const selectedSheet = ref('')
const docxHost = ref<HTMLElement | null>(null)
const pptxHost = ref<HTMLElement | null>(null)
const previewPanel = ref<HTMLElement | null>(null)
let currentUrl = ''
let sequence = 0
let pptxPreviewer: { preview: (buffer: ArrayBuffer) => Promise<void>; destroy?: () => void; renderNextSlide?: () => void; renderPreSlide?: () => void } | null = null
let pptxBuffer: ArrayBuffer | null = null
let bodyLocked = false

function extension(filename = '') {
  return filename.toLowerCase().split('.').pop() || ''
}
function previewKind(file: LensRunOutputFile | null) {
  if (!file || (file.byte_size && file.byte_size > PREVIEW_MAX_BYTES)) return ''
  const type = (file.content_type || '').toLowerCase()
  const ext = extension(file.filename)
  if (type.startsWith('image/') || ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)) return 'image'
  if (type === 'application/pdf' || ext === 'pdf') return 'pdf'
  if (type.includes('wordprocessingml') || ext === 'docx') return 'docx'
  if (type.includes('presentationml') || ext === 'pptx') return 'pptx'
  if (type.includes('spreadsheetml') || ext === 'xlsx') return 'xlsx'
  if (type === 'text/html' || ['html', 'htm'].includes(ext)) return 'html'
  if (type.startsWith('text/') || type === 'application/json' || ['md', 'markdown', 'txt', 'csv', 'json', 'log', 'yaml', 'yml', 'xml', 'toml', 'ini', 'css', 'sql', 'js', 'ts', 'jsx', 'tsx', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'h', 'sh', 'vue'].includes(ext)) return ext === 'md' || ext === 'markdown' ? 'markdown' : 'text'
  return ''
}
function clearPreview() {
  if (currentUrl) URL.revokeObjectURL(currentUrl)
  currentUrl = ''; objectUrl.value = ''; textContent.value = ''; workbook.value = null; sheetNames.value = []; selectedSheet.value = ''; pptxBuffer = null
  pptxPreviewer?.destroy?.(); pptxPreviewer = null
  if (docxHost.value) docxHost.value.innerHTML = ''
  if (pptxHost.value) pptxHost.value.innerHTML = ''
}
async function load(file: LensRunOutputFile | null) {
  const current = ++sequence
  clearPreview(); failed.value = false; kind.value = previewKind(file)
  if (!file || !kind.value) { failed.value = Boolean(file); return }
  loading.value = true
  try {
    const { blob } = await fetchCopilotAttachmentBlob(props.sessionId, file.uuid, file.url)
    if (current !== sequence) return
    if (kind.value === 'image' || kind.value === 'pdf') {
      currentUrl = URL.createObjectURL(blob); objectUrl.value = currentUrl
    } else if (kind.value === 'text' || kind.value === 'markdown') {
      const text = await blob.text()
      if (current !== sequence) return
      textContent.value = text
    } else if (kind.value === 'html') {
      currentUrl = URL.createObjectURL(new Blob([blob], { type: 'text/html' })); objectUrl.value = currentUrl
    } else if (kind.value === 'docx') {
      const { renderAsync } = await import('docx-preview')
      await nextTick(); if (current !== sequence || !docxHost.value) return
      await renderAsync(blob, docxHost.value, docxHost.value, { breakPages: true, inWrapper: true, renderComments: false })
    } else if (kind.value === 'pptx') {
      pptxBuffer = await blob.arrayBuffer(); await nextTick(); if (current !== sequence || !pptxHost.value) return
      const { init } = await import('pptx-preview')
      if (current !== sequence || !pptxHost.value) return
      pptxPreviewer = init(pptxHost.value, { width: 900, height: 620, mode: 'slide' })
      await pptxPreviewer.preview(pptxBuffer)
    } else if (kind.value === 'xlsx') {
      const XLSX = await import('xlsx')
      const parsed = XLSX.read(await blob.arrayBuffer(), { type: 'array', cellText: true, cellDates: true })
      if (current !== sequence) return
      workbook.value = { XLSX, sheets: parsed.Sheets as Record<string, unknown> }; sheetNames.value = parsed.SheetNames || []; selectedSheet.value = sheetNames.value[0] || ''
    }
  } catch { if (current === sequence) failed.value = true } finally { if (current === sequence) loading.value = false }
}
const selectedRows = computed(() => {
  if (!workbook.value || !selectedSheet.value) return [] as string[][]
  const rows = workbook.value.XLSX.utils.sheet_to_json(workbook.value.sheets[selectedSheet.value] as never, { header: 1, defval: '', raw: false }) as unknown[][]
  return rows.slice(0, 500).map((row) => row.map((cell) => String(cell ?? '')))
})
watch(() => props.file, load, { immediate: true })
onBeforeUnmount(() => { sequence++; clearPreview() })
async function close() {
  if (document.fullscreenElement && document.exitFullscreen) {
    await document.exitFullscreen().catch(() => undefined)
  }
  fullscreen.value = false
  emit('close')
}
function download() { if (props.file) emit('download', props.file) }
function slide(direction: number) { if (direction > 0) pptxPreviewer?.renderNextSlide?.(); else pptxPreviewer?.renderPreSlide?.() }

async function toggleFullscreen() {
  if (fullscreen.value) {
    if (document.fullscreenElement && document.exitFullscreen) {
      await document.exitFullscreen().catch(() => undefined)
    }
    fullscreen.value = false
    return
  }
  fullscreen.value = true
  const request = previewPanel.value?.requestFullscreen?.()
  await request?.catch(() => undefined)
}

function handleKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') {
    if (fullscreen.value) {
      void toggleFullscreen()
    } else {
      close()
    }
  } else if (kind.value === 'pptx' && [' ', 'ArrowLeft', 'ArrowRight'].includes(event.key)) {
    event.preventDefault()
    slide(event.key === 'ArrowLeft' ? -1 : 1)
  }
}

function handleFullscreenChange() {
  fullscreen.value = Boolean(document.fullscreenElement)
  if (kind.value === 'pptx' && pptxBuffer) {
    void renderPptxForCurrentSize()
  }
}

async function renderPptxForCurrentSize() {
  const buffer = pptxBuffer
  if (!pptxHost.value || !buffer || !pptxPreviewer) return
  const current = ++sequence
  await nextTick()
  if (current !== sequence || !pptxHost.value || !pptxPreviewer || pptxBuffer !== buffer) return
  pptxPreviewer.destroy?.()
  pptxHost.value.innerHTML = ''
  const { init } = await import('pptx-preview')
  if (current !== sequence || !pptxHost.value || pptxBuffer !== buffer) return
  pptxPreviewer = init(pptxHost.value, {
    width: previewPanel.value?.clientWidth || 900,
    height: Math.max((previewPanel.value?.clientHeight || 620) - 56, 240),
    mode: 'slide',
  })
  await pptxPreviewer.preview(buffer)
}

let previousOverflow = ''
let listenersAttached = false
watch(() => props.file, (file) => {
  if (listenersAttached) {
    document.removeEventListener('keydown', handleKeydown)
    document.removeEventListener('fullscreenchange', handleFullscreenChange)
    listenersAttached = false
  }
  if (file) {
    if (!bodyLocked) {
      previousOverflow = document.body.style.overflow
      bodyLocked = true
    }
    document.body.style.overflow = 'hidden'
    document.addEventListener('keydown', handleKeydown)
    document.addEventListener('fullscreenchange', handleFullscreenChange)
    listenersAttached = true
  } else {
    if (bodyLocked) {
      document.body.style.overflow = previousOverflow
      bodyLocked = false
    }
    document.removeEventListener('keydown', handleKeydown)
    document.removeEventListener('fullscreenchange', handleFullscreenChange)
  }
}, { immediate: true })

onBeforeUnmount(() => {
  if (bodyLocked) document.body.style.overflow = previousOverflow
  document.removeEventListener('keydown', handleKeydown)
  document.removeEventListener('fullscreenchange', handleFullscreenChange)
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="file"
      class="copilot-preview-backdrop"
      role="presentation"
      @click.self="close"
    >
      <section
        ref="previewPanel"
        class="copilot-preview"
        :class="{ 'is-fullscreen': fullscreen }"
        role="dialog"
        aria-modal="true"
        :aria-label="file.filename"
      >
        <header class="copilot-preview__header">
          <strong class="copilot-preview__title">{{ file.filename }}</strong>
          <div class="copilot-preview__actions">
            <button
              type="button"
              :title="fullscreen ? t('insight.copilot.outputFileExitFullscreen') : t('insight.copilot.outputFileFullscreen')"
              :aria-label="fullscreen ? t('insight.copilot.outputFileExitFullscreen') : t('insight.copilot.outputFileFullscreen')"
              @click="toggleFullscreen"
            >
              <Minimize2
                v-if="fullscreen"
                :size="17"
              /><Maximize2
                v-else
                :size="17"
              />
            </button>
            <button
              type="button"
              :title="t('insight.copilot.outputFileDownload')"
              :aria-label="t('insight.copilot.outputFileDownload')"
              @click="download"
            >
              <Download :size="17" />
            </button>
            <button
              type="button"
              :title="t('common.close')"
              :aria-label="t('common.close')"
              @click="close"
            >
              <X :size="18" />
            </button>
          </div>
        </header>
        <div class="copilot-preview__body">
          <div
            v-if="kind === 'docx'"
            ref="docxHost"
            class="copilot-preview__office"
          />
          <div
            v-if="kind === 'pptx'"
            class="copilot-preview__pptx-wrap"
          >
            <div
              ref="pptxHost"
              class="copilot-preview__pptx"
            />
            <div class="copilot-preview__slide-actions">
              <button
                type="button"
                :aria-label="t('insight.copilot.outputFilePreviousSlide')"
                @click="slide(-1)"
              >
                <ChevronLeft :size="18" />
              </button>
              <button
                type="button"
                :aria-label="t('insight.copilot.outputFileNextSlide')"
                @click="slide(1)"
              >
                <ChevronRight :size="18" />
              </button>
            </div>
          </div>
          <div
            v-if="loading"
            class="copilot-preview__state"
            role="status"
          >
            {{ t('insight.copilot.outputFilePreviewLoading') }}
          </div>
          <div
            v-else-if="failed"
            class="copilot-preview__state copilot-preview__state--error"
          >
            {{ file.byte_size && file.byte_size > PREVIEW_MAX_BYTES ? t('insight.copilot.outputFilePreviewTooLarge') : t('insight.copilot.outputFilePreviewFailed') }}
          </div>
          <img
            v-else-if="kind === 'image'"
            :src="objectUrl"
            :alt="file.filename"
            class="copilot-preview__image"
          >
          <iframe
            v-else-if="kind === 'pdf'"
            :src="objectUrl"
            :title="file.filename"
            class="copilot-preview__frame"
          />
          <iframe
            v-else-if="kind === 'html'"
            :src="objectUrl"
            :title="file.filename"
            class="copilot-preview__frame"
            sandbox=""
          />
          <CopilotMarkdown
            v-else-if="kind === 'markdown'"
            :content="textContent"
          />
          <pre
            v-else-if="kind === 'text'"
            class="copilot-preview__text"
          ><code>{{ textContent }}</code></pre>
          <div
            v-else-if="kind === 'xlsx'"
            class="copilot-preview__sheet-wrap"
          >
            <nav
              class="copilot-preview__sheets"
              role="tablist"
              :aria-label="t('insight.copilot.outputFileSheet')"
            >
              <button
                v-for="name in sheetNames"
                :key="name"
                type="button"
                :class="{ active: selectedSheet === name }"
                role="tab"
                :aria-selected="selectedSheet === name"
                @click="selectedSheet = name"
              >
                {{ name }}
              </button>
            </nav><table class="copilot-preview__sheet">
              <tbody>
                <tr
                  v-for="(row, index) in selectedRows"
                  :key="index"
                >
                  <td
                    v-for="(cell, cellIndex) in row"
                    :key="cellIndex"
                  >
                    {{ cell }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.copilot-preview-backdrop { position: fixed; inset: 0; z-index: 70; display: grid; place-items: center; padding: 24px; background: rgb(15 23 42 / 55%); }
.copilot-preview { display: flex; width: min(1080px, 100%); height: min(780px, 90vh); flex-direction: column; overflow: hidden; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-card-bg); box-shadow: var(--shadow-lg); }
.copilot-preview.is-fullscreen { width: 100%; height: 100%; border-radius: 0; }
.copilot-preview__header { display: flex; min-height: 56px; align-items: center; justify-content: space-between; gap: 12px; padding: 12px 18px; border-bottom: 1px solid var(--color-border); }
.copilot-preview__title { min-width: 0; overflow: hidden; color: var(--color-text-title); text-overflow: ellipsis; white-space: nowrap; }
.copilot-preview__actions { display: flex; flex-shrink: 0; gap: 4px; }
.copilot-preview__actions button, .copilot-preview__slide-actions button { display: grid; min-width: 40px; min-height: 40px; place-items: center; border: 0; border-radius: 6px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.copilot-preview__actions button:hover, .copilot-preview__slide-actions button:hover { background: var(--color-grey-1); color: var(--color-primary); }
.copilot-preview__body { min-height: 0; flex: 1; overflow: auto; padding: 20px; }
.copilot-preview__state { display: grid; min-height: 240px; place-items: center; color: var(--color-text-secondary); text-align: center; }
.copilot-preview__state--error { color: var(--color-danger, #dc2626); }
.copilot-preview__image { display: block; max-width: 100%; max-height: 100%; margin: auto; object-fit: contain; }
.copilot-preview__frame { width: 100%; height: 100%; min-height: 520px; border: 0; }
.copilot-preview__text { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; font: 13px/1.6 var(--font-mono); }
.copilot-preview__office, .copilot-preview__pptx { min-height: 500px; background: #fff; color: #111; }
.copilot-preview__pptx-wrap { position: relative; }
.copilot-preview__slide-actions { display: flex; justify-content: center; gap: 8px; margin-top: 10px; }
.copilot-preview__sheets { display: flex; gap: 4px; margin-bottom: 12px; overflow-x: auto; }
.copilot-preview__sheets button { padding: 8px 12px; border: 1px solid var(--color-border); border-radius: 6px; background: transparent; color: var(--color-text-secondary); cursor: pointer; }
.copilot-preview__sheets button.active { border-color: var(--color-primary); color: var(--color-primary); }
.copilot-preview__sheet { border-collapse: collapse; font-size: 13px; }
.copilot-preview__sheet td { min-width: 80px; padding: 7px 9px; border: 1px solid var(--color-border); white-space: pre-wrap; }
@media (max-width: 640px) { .copilot-preview-backdrop { padding: 0; } .copilot-preview { height: 100%; border-radius: 0; } .copilot-preview__body { padding: 12px; } }
</style>

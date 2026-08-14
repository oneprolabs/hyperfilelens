<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Download, FileText } from 'lucide-vue-next'
import {
  fetchCopilotAttachmentBlob,
  type LensChatAttachment,
} from '../../../lib/lensApi'

const props = defineProps<{
  sessionId: number
  attachments?: LensChatAttachment[]
}>()

const { t } = useI18n()
const containerRef = ref<HTMLElement | null>(null)
const imageUrls = ref<Record<string, string>>({})
const failedImages = ref<Set<string>>(new Set())
const imageLoadingEnabled = ref(false)
let loadGeneration = 0
let imageAbortController: AbortController | null = null
let visibilityObserver: IntersectionObserver | null = null

const orderedAttachments = computed(() =>
  [...(props.attachments || [])].sort(
    (left, right) => (left.order ?? 0) - (right.order ?? 0),
  ),
)

const attachmentSignature = computed(() =>
  orderedAttachments.value.map((item) => `${item.uuid}:${item.kind}`).join('|'),
)

function isImage(item: LensChatAttachment) {
  return item.kind === 'image' || item.mime_type?.startsWith('image/')
}

function revokeUrls(urls: Record<string, string>) {
  for (const url of Object.values(urls)) URL.revokeObjectURL(url)
}

function revokeImageUrls() {
  revokeUrls(imageUrls.value)
  imageUrls.value = {}
}

async function loadImages() {
  const generation = ++loadGeneration
  imageAbortController?.abort()
  const abortController = new AbortController()
  imageAbortController = abortController
  revokeImageUrls()
  failedImages.value = new Set()
  const nextUrls: Record<string, string> = {}
  const nextFailures = new Set<string>()
  let committed = false
  try {
    for (const item of orderedAttachments.value.filter(isImage)) {
      try {
        const { blob } = await fetchCopilotAttachmentBlob(
          props.sessionId,
          item.uuid,
          item.url,
          abortController.signal,
        )
        nextUrls[item.uuid] = URL.createObjectURL(blob)
        if (generation !== loadGeneration || abortController.signal.aborted) return
      } catch {
        if (abortController.signal.aborted) return
        nextFailures.add(item.uuid)
      }
    }
    if (generation !== loadGeneration) return
    imageUrls.value = nextUrls
    failedImages.value = nextFailures
    committed = true
  } finally {
    if (!committed) revokeUrls(nextUrls)
  }
}

function formatBytes(value?: number) {
  const bytes = Number(value || 0)
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function fileLabel(item: LensChatAttachment) {
  const name = item.original_name || t('insight.copilot.attachmentDocument')
  const size = formatBytes(item.byte_size)
  return size ? `${name} · ${size}` : name
}

async function downloadAttachment(item: LensChatAttachment) {
  try {
    const { blob, filename } = await fetchCopilotAttachmentBlob(
      props.sessionId,
      item.uuid,
      item.url,
    )
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename || item.original_name || 'attachment'
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch {
    ElMessage.error({
      message: t('insight.copilot.attachmentDownloadFailed'),
      grouping: true,
    })
  }
}

watch(
  [() => props.sessionId, attachmentSignature, imageLoadingEnabled],
  () => {
    if (imageLoadingEnabled.value) void loadImages()
  },
)

onMounted(() => {
  const container = containerRef.value
  if (!container || typeof IntersectionObserver === 'undefined') {
    imageLoadingEnabled.value = true
    return
  }
  visibilityObserver = new IntersectionObserver(
    (entries) => {
      if (!entries.some((entry) => entry.isIntersecting)) return
      imageLoadingEnabled.value = true
      visibilityObserver?.disconnect()
      visibilityObserver = null
    },
    { rootMargin: '240px 0px' },
  )
  visibilityObserver.observe(container)
})

onBeforeUnmount(() => {
  loadGeneration += 1
  imageAbortController?.abort()
  visibilityObserver?.disconnect()
  revokeImageUrls()
})
</script>

<template>
  <div
    v-if="orderedAttachments.length"
    ref="containerRef"
    class="copilot-message-attachments"
  >
    <template
      v-for="item in orderedAttachments"
      :key="item.uuid"
    >
      <ElImage
        v-if="isImage(item) && imageUrls[item.uuid]"
        class="copilot-message-image"
        :src="imageUrls[item.uuid]"
        :preview-src-list="[imageUrls[item.uuid] || '']"
        :alt="item.original_name || t('insight.copilot.attachmentImage')"
        fit="cover"
        hide-on-click-modal
        preview-teleported
      />
      <div
        v-else-if="isImage(item)"
        class="copilot-message-image copilot-message-image--loading"
        :class="{ 'is-error': failedImages.has(item.uuid) }"
        :role="imageLoadingEnabled ? 'status' : undefined"
      >
        {{
          failedImages.has(item.uuid)
            ? t('insight.copilot.attachmentLoadFailed')
            : imageLoadingEnabled
              ? t('insight.copilot.attachmentLoading')
              : ''
        }}
      </div>
      <button
        v-else
        type="button"
        class="copilot-message-document"
        :title="fileLabel(item)"
        @click="downloadAttachment(item)"
      >
        <FileText
          :size="20"
          aria-hidden="true"
        />
        <span class="copilot-message-document__name">{{ fileLabel(item) }}</span>
        <Download
          :size="16"
          aria-hidden="true"
        />
      </button>
    </template>
  </div>
</template>

<style scoped>
.copilot-message-attachments {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 10px;
}

.copilot-message-image {
  width: min(240px, 60vw);
  height: 160px;
  overflow: hidden;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-grey-2);
}

.copilot-message-image--loading {
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 12px;
  color: var(--color-text-tertiary);
  font-size: 12px;
  text-align: center;
}

.copilot-message-image--loading.is-error {
  color: var(--color-danger);
}

.copilot-message-document {
  display: inline-flex;
  min-width: 0;
  max-width: min(360px, 70vw);
  min-height: 48px;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-card-bg);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease, color 0.15s ease;
}

.copilot-message-document:hover {
  border-color: color-mix(in srgb, var(--color-primary) 40%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 4%, var(--color-card-bg));
  color: var(--color-text-primary);
}

.copilot-message-document:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent);
  outline-offset: 2px;
}

.copilot-message-document__name {
  min-width: 0;
  overflow: hidden;
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>

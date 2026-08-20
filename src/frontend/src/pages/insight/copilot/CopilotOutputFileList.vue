<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Download, FileText } from 'lucide-vue-next'
import {
  fetchCopilotAttachmentBlob,
  type LensRunOutputFile,
} from '../../../lib/lensApi'

const props = defineProps<{
  sessionId: number
  files?: LensRunOutputFile[]
}>()

const { t } = useI18n()

const orderedFiles = computed(() => [...(props.files || [])])

function formatBytes(value?: number) {
  const bytes = Number(value || 0)
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function fileLabel(file: LensRunOutputFile) {
  const name = file.filename || t('insight.copilot.outputFile')
  const size = formatBytes(file.byte_size)
  return size ? `${name} · ${size}` : name
}

async function downloadFile(file: LensRunOutputFile) {
  try {
    const { blob, filename } = await fetchCopilotAttachmentBlob(
      props.sessionId,
      file.uuid,
      file.url,
    )
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename || file.filename || 'output-file'
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
</script>

<template>
  <div
    v-if="orderedFiles.length"
    class="copilot-output-files"
  >
    <div class="copilot-output-files__label">
      {{ t('insight.copilot.outputFileTitle') }}
    </div>
    <button
      v-for="file in orderedFiles"
      :key="file.uuid"
      type="button"
      class="copilot-output-file"
      :title="fileLabel(file)"
      @click="downloadFile(file)"
    >
      <FileText
        :size="20"
        aria-hidden="true"
      />
      <span class="copilot-output-file__name">{{ fileLabel(file) }}</span>
      <Download
        :size="16"
        aria-hidden="true"
      />
    </button>
  </div>
</template>

<style scoped>
.copilot-output-files {
  display: flex;
  flex-direction: column;
  gap: 8px;
  width: 100%;
  margin: 16px 0 4px;
}

.copilot-output-files__label {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.copilot-output-file {
  display: inline-flex;
  min-width: 0;
  max-width: min(420px, 100%);
  min-height: 48px;
  align-items: center;
  gap: 10px;
  padding: 8px 12px;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  background: var(--color-card-bg);
  color: var(--color-text-secondary);
  cursor: pointer;
  transition: border-color 0.15s ease, background 0.15s ease, color 0.15s ease;
}

.copilot-output-file:hover {
  border-color: color-mix(in srgb, var(--color-primary) 40%, var(--color-border));
  background: color-mix(in srgb, var(--color-primary) 4%, var(--color-card-bg));
  color: var(--color-text-primary);
}

.copilot-output-file:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent);
  outline-offset: 2px;
}

.copilot-output-file__name {
  min-width: 0;
  overflow: hidden;
  flex: 1;
  font-size: 13px;
  font-weight: 600;
  text-overflow: ellipsis;
  white-space: nowrap;
}

@media (prefers-reduced-motion: reduce) {
  .copilot-output-file {
    transition: none;
  }
}

@media (max-width: 768px) {
  .copilot-output-file {
    width: 100%;
    min-height: 52px;
  }
}
</style>

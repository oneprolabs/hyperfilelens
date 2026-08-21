<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ArrowUp, FileText, LoaderCircle, Plus, Square, X } from 'lucide-vue-next'
import type { CopilotComposerAttachment } from './types'

const SOURCE_LENS_MAX_ATTACHMENTS = 4
const SOURCE_LENS_IMAGE_ATTACHMENTS = [
  'image/png',
  'image/jpeg',
  'image/webp',
  'image/gif',
]
const SOURCE_LENS_DOCUMENT_ATTACHMENTS = [
  '.pdf',
  '.docx',
  '.pptx',
  '.xlsx',
]

const props = defineProps<{
  modelValue: string
  attachments?: CopilotComposerAttachment[]
  sending?: boolean
  canStop?: boolean
  disabled?: boolean
  supportsImages?: boolean
  supportsDocuments?: boolean
}>()

const emit = defineEmits<{
  'update:modelValue': [value: string]
  send: []
  stop: []
  attach: [files: File[]]
  removeAttachment: [attachment: CopilotComposerAttachment]
  resize: [height: number]
}>()

const { t } = useI18n()
const fieldRef = ref<HTMLTextAreaElement | null>(null)
const fileInputRef = ref<HTMLInputElement | null>(null)
const composerRef = ref<HTMLElement | null>(null)
let composerResizeObserver: ResizeObserver | null = null

const hasUploadingAttachment = computed(() =>
  (props.attachments || []).some((item) => item.status === 'uploading'),
)

const canSend = computed(() =>
  !props.disabled
  && !props.sending
  && !hasUploadingAttachment.value
  && (Boolean(props.modelValue.trim()) || Boolean(props.attachments?.length)),
)

const acceptedAttachments = computed(() => [
  ...(props.supportsImages ? SOURCE_LENS_IMAGE_ATTACHMENTS : []),
  ...(props.supportsDocuments ? SOURCE_LENS_DOCUMENT_ATTACHMENTS : []),
].join(','))

const canAttach = computed(() =>
  !props.disabled
  && Boolean(acceptedAttachments.value)
  && (props.attachments?.length || 0) < SOURCE_LENS_MAX_ATTACHMENTS,
)

function resizeField() {
  const el = fieldRef.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
}

function onInput(event: Event) {
  const value = (event.target as HTMLTextAreaElement).value
  emit('update:modelValue', value)
  resizeField()
}

function chooseAttachments() {
  if (canAttach.value) fileInputRef.value?.click()
}

function onFileChange(event: Event) {
  const input = event.target as HTMLInputElement
  const files = Array.from(input.files || [])
  input.value = ''
  if (files.length) emit('attach', files)
}

function onPaste(event: ClipboardEvent) {
  const images = Array.from(event.clipboardData?.files || []).filter((file) =>
    file.type.startsWith('image/'),
  )
  if (!images.length || !canAttach.value || !props.supportsImages) return
  event.preventDefault()
  emit('attach', images)
}

function submit() {
  if (canSend.value) emit('send')
}

function formatBytes(value?: number) {
  const bytes = Number(value || 0)
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

watch(
  () => props.modelValue,
  () => nextTick(resizeField),
)

function reportComposerHeight() {
  const height = Math.ceil(composerRef.value?.getBoundingClientRect().height || 0)
  if (height > 0) emit('resize', height)
}

onMounted(() => {
  void nextTick(() => {
    reportComposerHeight()
    if (typeof ResizeObserver === 'undefined' || !composerRef.value) return
    composerResizeObserver = new ResizeObserver(reportComposerHeight)
    composerResizeObserver.observe(composerRef.value)
  })
})

onBeforeUnmount(() => {
  composerResizeObserver?.disconnect()
  composerResizeObserver = null
})
</script>

<template>
  <footer
    ref="composerRef"
    class="copilot-composer"
  >
    <input
      ref="fileInputRef"
      class="sr-only"
      type="file"
      multiple
      :accept="acceptedAttachments"
      :disabled="!canAttach"
      @change="onFileChange"
    >

    <div
      class="copilot-input-shell"
      :class="{ 'is-disabled': disabled }"
    >
      <div
        v-if="attachments?.length"
        class="copilot-attachment-strip"
      >
        <div
          v-for="item in attachments"
          :key="item.key"
          class="copilot-attachment-card"
        >
          <img
            v-if="item.kind === 'image' && item.localUrl"
            class="copilot-attachment-card__preview"
            :src="item.localUrl"
            :alt="item.original_name || t('insight.copilot.attachmentImage')"
          >
          <span
            v-else
            class="copilot-attachment-card__file"
            aria-hidden="true"
          >
            <FileText :size="20" />
          </span>
          <span class="copilot-attachment-card__body">
            <span class="copilot-attachment-card__name">
              {{ item.original_name || t('insight.copilot.attachmentDocument') }}
            </span>
            <span class="copilot-attachment-card__meta">
              <template v-if="item.status === 'uploading'">
                <LoaderCircle
                  :size="12"
                  class="copilot-attachment-spinner"
                />
                {{ t('insight.copilot.attachmentUploading') }}
              </template>
              <template v-else>{{ formatBytes(item.byte_size) }}</template>
            </span>
          </span>
          <button
            type="button"
            class="copilot-attachment-card__remove"
            :aria-label="t('insight.copilot.attachmentRemove')"
            :title="t('insight.copilot.attachmentRemove')"
            @click="emit('removeAttachment', item)"
          >
            <X :size="14" />
          </button>
        </div>
      </div>

      <div class="copilot-input-row">
        <button
          type="button"
          class="copilot-attach-btn"
          :disabled="!canAttach"
          :aria-label="t('insight.copilot.attach')"
          :title="t('insight.copilot.attach')"
          @click="chooseAttachments"
        >
          <Plus
            :size="20"
            :stroke-width="2.1"
          />
        </button>

        <textarea
          ref="fieldRef"
          :value="modelValue"
          rows="1"
          :placeholder="t('insight.copilot.inputPlaceholder')"
          :aria-label="t('insight.copilot.inputPlaceholder')"
          :disabled="disabled"
          class="copilot-input-field"
          @input="onInput"
          @paste="onPaste"
          @keydown.enter.exact.prevent="submit"
        />

        <button
          v-if="sending"
          type="button"
          class="copilot-send-btn copilot-send-btn--stop"
          :disabled="!canStop"
          :title="t('common.stop')"
          :aria-label="t('common.stop')"
          @click="emit('stop')"
        >
          <Square :size="14" />
        </button>
        <button
          v-else
          type="button"
          class="copilot-send-btn"
          :class="{ 'is-active': canSend }"
          :disabled="!canSend"
          :title="t('insight.copilot.send')"
          :aria-label="t('insight.copilot.send')"
          @click="submit"
        >
          <ArrowUp
            :size="18"
            :stroke-width="2.25"
          />
        </button>
      </div>
    </div>

    <p class="copilot-disclaimer">
      {{ t('insight.copilot.disclaimer') }}
    </p>
  </footer>
</template>

<style scoped>
.copilot-composer {
  position: absolute;
  z-index: 5;
  right: 0;
  bottom: 0;
  left: 0;
  padding: 24px 28px 14px;
  pointer-events: none;
  background: transparent;
}

.copilot-input-shell {
  display: flex;
  width: 100%;
  max-width: 860px;
  flex-direction: column;
  min-height: 58px;
  margin: 0 auto;
  padding: 8px;
  pointer-events: auto;
  background: var(--color-card-bg, #fff);
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 16px;
  box-shadow: 0 4px 14px rgba(15, 23, 42, 0.08);
  transition: border-color 0.15s ease, box-shadow 0.15s ease;
}

.copilot-input-shell:focus-within:not(.is-disabled) {
  border-color: var(--color-border-strong, #cbd5e1);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary, #6366f1) 8%, transparent);
}

.copilot-input-shell.is-disabled {
  opacity: 0.65;
}

.copilot-attachment-strip {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  padding: 2px 2px 8px;
}

.copilot-attachment-card {
  position: relative;
  display: flex;
  width: min(220px, 100%);
  min-height: 54px;
  align-items: center;
  gap: 9px;
  padding: 6px 34px 6px 6px;
  border: 1px solid var(--color-border-light);
  border-radius: 10px;
  background: var(--color-grey-1);
}

.copilot-attachment-card__preview,
.copilot-attachment-card__file {
  width: 40px;
  height: 40px;
  flex: 0 0 40px;
  border-radius: 7px;
  background: var(--color-grey-2);
}

.copilot-attachment-card__preview {
  object-fit: cover;
}

.copilot-attachment-card__file {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--color-text-secondary);
}

.copilot-attachment-card__body {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 3px;
}

.copilot-attachment-card__name {
  overflow: hidden;
  color: var(--color-text-primary);
  font-size: 12px;
  font-weight: 600;
  line-height: 16px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.copilot-attachment-card__meta {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  min-height: 14px;
  color: var(--color-text-tertiary);
  font-size: 11px;
}

.copilot-attachment-spinner {
  animation: copilot-attachment-spin 0.8s linear infinite;
}

.copilot-attachment-card__remove {
  position: absolute;
  top: 5px;
  right: 5px;
  display: inline-flex;
  width: 28px;
  height: 28px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 7px;
  background: transparent;
  color: var(--color-text-tertiary);
  cursor: pointer;
}

.copilot-attachment-card__remove::before {
  position: absolute;
  inset: -8px;
  content: '';
}

.copilot-attachment-card__remove:hover {
  background: var(--color-grey-3);
  color: var(--color-text-primary);
}

.copilot-input-row {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}

.copilot-input-field {
  flex: 1;
  min-width: 0;
  min-height: 40px;
  max-height: 200px;
  padding: 9px 2px;
  margin: 0;
  border: none;
  outline: none;
  resize: none;
  background: transparent;
  color: var(--color-text-primary);
  font: inherit;
  font-size: 16px;
  line-height: 1.45;
}

.copilot-input-field::placeholder {
  color: var(--color-text-tertiary, #94a3b8);
}

.copilot-input-field:disabled {
  cursor: not-allowed;
}

.copilot-attach-btn,
.copilot-send-btn {
  display: inline-flex;
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: none;
  border-radius: 999px;
  transition: background 0.15s ease, color 0.15s ease, transform 0.1s ease;
}

.copilot-attach-btn {
  background: transparent;
  color: var(--color-text-secondary);
  cursor: pointer;
}

.copilot-attach-btn:hover:not(:disabled) {
  background: var(--color-grey-2);
  color: var(--color-text-primary);
}

.copilot-attach-btn:disabled {
  color: var(--color-text-disabled);
  cursor: not-allowed;
}

.copilot-send-btn {
  background: var(--color-grey-2, #f1f5f9);
  color: var(--color-text-disabled, #cbd5e1);
  cursor: not-allowed;
}

.copilot-send-btn.is-active {
  background: var(--color-primary, #6d5bd0);
  color: #ffffff;
  cursor: pointer;
}

.copilot-send-btn.is-active:hover {
  background: color-mix(in srgb, var(--color-primary, #6d5bd0) 84%, #000);
  color: #ffffff;
}

.copilot-send-btn.is-active:active,
.copilot-attach-btn:active:not(:disabled) {
  transform: scale(0.96);
}

.copilot-send-btn--stop {
  background: var(--color-primary, #6d5bd0);
  color: #ffffff;
  cursor: pointer;
}

.copilot-send-btn--stop:hover:not(:disabled) {
  background: color-mix(in srgb, var(--color-primary, #6d5bd0) 84%, #000);
}

.copilot-send-btn--stop:disabled {
  background: color-mix(in srgb, var(--color-primary, #6d5bd0) 52%, #ffffff);
  color: rgba(255, 255, 255, 0.82);
  cursor: wait;
}

.copilot-attach-btn:focus-visible,
.copilot-send-btn:focus-visible,
.copilot-attachment-card__remove:focus-visible {
  outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent);
  outline-offset: 2px;
}

.copilot-disclaimer {
  width: 100%;
  max-width: 860px;
  margin: 8px auto 0;
  color: var(--color-text-tertiary);
  font-size: 11px;
  line-height: 16px;
  text-align: center;
}

@keyframes copilot-attachment-spin {
  to { transform: rotate(360deg); }
}

@media (prefers-reduced-motion: reduce) {
  .copilot-attachment-spinner {
    animation: none;
  }

  .copilot-input-shell,
  .copilot-attach-btn,
  .copilot-send-btn {
    transition: none;
  }
}

@media (max-width: 767.98px) {
  .copilot-composer {
    padding: 28px 12px calc(12px + env(safe-area-inset-bottom));
    background: transparent;
  }
}
</style>

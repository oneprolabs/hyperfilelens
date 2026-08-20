<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute, useRouter } from 'vue-router'
import { ArrowLeft, Download, FileText, Sparkles } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import CopilotMarkdown from '../../components/copilot/CopilotMarkdown.vue'
import { apiErrorMessage } from '../../lib/api'
import {
  fetchSharedCopilotFile,
  fetchSharedCopilotQA,
  setLensApiScope,
  type LensSharedQA,
  type LensSharedQAFile,
} from '../../lib/lensApi'

defineOptions({ name: 'SharedCopilotQA' })

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const loading = ref(true)
const share = ref<LensSharedQA | null>(null)
const error = ref('')
const downloading = ref<Set<string>>(new Set())
let loadGeneration = 0

const access = computed(() => String(route.query.access || ''))

function formattedPublishedAt(value?: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleString()
}

function formatBytes(value?: number) {
  const bytes = Number(value || 0)
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function fileLabel(file: LensSharedQAFile) {
  const size = formatBytes(file.byte_size)
  return size ? `${file.filename} · ${size}` : file.filename
}

async function load() {
  const requestedAccess = access.value
  const generation = ++loadGeneration
  loading.value = true
  error.value = ''
  share.value = null
  setLensApiScope('tenant')
  if (!requestedAccess) {
    error.value = t('insight.copilot.sharedNotFound')
    loading.value = false
    return
  }
  try {
    const result = await fetchSharedCopilotQA(requestedAccess)
    if (generation !== loadGeneration || access.value !== requestedAccess) return
    share.value = result
  } catch (reason) {
    if (generation !== loadGeneration) return
    error.value = apiErrorMessage(reason, t('insight.copilot.sharedNotFound'))
  } finally {
    if (generation === loadGeneration) loading.value = false
  }
}

async function download(url: string, fallbackName: string, key: string) {
  if (!url || downloading.value.has(key)) return
  downloading.value = new Set(downloading.value).add(key)
  try {
    const result = await fetchSharedCopilotFile(url)
    const href = URL.createObjectURL(result.blob)
    const anchor = document.createElement('a')
    anchor.href = href
    anchor.download = result.filename || fallbackName
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    URL.revokeObjectURL(href)
  } catch (reason) {
    ElMessage.error({
      message: apiErrorMessage(reason, t('insight.copilot.sharedFileDownloadFailed')),
      grouping: true,
    })
  } finally {
    const next = new Set(downloading.value)
    next.delete(key)
    downloading.value = next
  }
}

onMounted(load)
watch(access, load)
</script>

<template>
  <main class="shared-qa-page">
    <div class="shared-qa-page__shell">
      <button
        class="shared-qa-page__back"
        type="button"
        @click="router.push('/insight/copilot')"
      >
        <ArrowLeft
          :size="17"
          aria-hidden="true"
        />
        {{ t('insight.copilot.sharedBackToChats') }}
      </button>

      <div
        v-if="loading"
        v-loading="true"
        class="shared-qa-page__state"
      />

      <ElResult
        v-else-if="error || !share"
        icon="warning"
        :title="t('insight.copilot.sharedNotFoundTitle')"
        :sub-title="error || t('insight.copilot.sharedNotFound')"
      >
        <template #extra>
          <ElButton
            type="primary"
            @click="router.push('/insight/copilot')"
          >
            {{ t('insight.copilot.sharedBackToChats') }}
          </ElButton>
        </template>
      </ElResult>

      <article
        v-else
        class="shared-qa-card"
      >
        <header class="shared-qa-card__header">
          <span class="shared-qa-card__icon">
            <Sparkles
              :size="20"
              aria-hidden="true"
            />
          </span>
          <div>
            <p>{{ share.assistant_name || t('insight.copilot.shareGenericAssistant') }}</p>
            <h1>{{ share.title || t('insight.copilot.shareTitle') }}</h1>
            <time v-if="share.published_at">{{ formattedPublishedAt(share.published_at) }}</time>
          </div>
          <ElButton
            v-if="share.pdf_url"
            class="shared-qa-card__pdf"
            plain
            :loading="downloading.has('pdf')"
            @click="download(share.pdf_url, 'shared-answer.pdf', 'pdf')"
          >
            <Download
              :size="16"
              aria-hidden="true"
            />
            {{ t('insight.copilot.downloadPdf') }}
          </ElButton>
        </header>

        <section class="shared-qa-card__question">
          <span>{{ t('insight.copilot.sharedQuestion') }}</span>
          <p>{{ share.question }}</p>
          <div
            v-if="share.input_attachments?.length"
            class="shared-qa-files"
          >
            <button
              v-for="file in share.input_attachments"
              :key="file.uuid"
              type="button"
              @click="download(file.url, file.filename, file.uuid)"
            >
              <FileText
                :size="17"
                aria-hidden="true"
              />
              <span>{{ fileLabel(file) }}</span>
              <Download
                :size="15"
                aria-hidden="true"
              />
            </button>
          </div>
        </section>

        <section class="shared-qa-card__answer">
          <span>{{ t('insight.copilot.sharedAnswer') }}</span>
          <CopilotMarkdown :content="share.answer" />
          <div
            v-if="share.output_files?.length"
            class="shared-qa-files shared-qa-files--output"
          >
            <button
              v-for="file in share.output_files"
              :key="file.uuid"
              type="button"
              @click="download(file.url, file.filename, file.uuid)"
            >
              <FileText
                :size="17"
                aria-hidden="true"
              />
              <span>{{ fileLabel(file) }}</span>
              <Download
                :size="15"
                aria-hidden="true"
              />
            </button>
          </div>
        </section>

        <footer>{{ t('insight.copilot.disclaimer') }}</footer>
      </article>
    </div>
  </main>
</template>

<style scoped>
.shared-qa-page { width: 100%; min-height: 100%; padding: 28px 20px 56px; overflow-y: auto; background: var(--color-grey-2); }
.shared-qa-page__shell { width: min(900px, 100%); margin: 0 auto; }
.shared-qa-page__back { display: inline-flex; min-height: 40px; align-items: center; gap: 7px; margin-bottom: 16px; padding: 0 10px; border: 0; border-radius: 8px; background: transparent; color: var(--color-text-secondary); cursor: pointer; font: inherit; }
.shared-qa-page__back:hover { background: var(--color-card-bg); color: var(--color-primary); }
.shared-qa-page__state { min-height: 360px; }
.shared-qa-card { overflow: hidden; border: 1px solid var(--color-border); border-radius: 16px; background: var(--color-card-bg); box-shadow: 0 12px 36px rgb(15 23 42 / 7%); }
.shared-qa-card__header { display: grid; grid-template-columns: auto minmax(0, 1fr) auto; align-items: start; gap: 14px; padding: 24px 28px; border-bottom: 1px solid var(--color-border); }
.shared-qa-card__icon { display: inline-flex; width: 42px; height: 42px; align-items: center; justify-content: center; border-radius: 12px; background: color-mix(in srgb, var(--color-primary) 10%, var(--color-card-bg)); color: var(--color-primary); }
.shared-qa-card__header p { margin: 0 0 4px; color: var(--color-primary); font-size: 12px; font-weight: 600; }
.shared-qa-card__header h1 { margin: 0; color: var(--color-text-title); font-size: clamp(20px, 3vw, 28px); line-height: 1.3; }
.shared-qa-card__header time { display: block; margin-top: 7px; color: var(--color-text-tertiary); font-size: 12px; }
.shared-qa-card__question,.shared-qa-card__answer { padding: 24px 28px; }
.shared-qa-card__question { border-bottom: 1px solid var(--color-border); background: color-mix(in srgb, var(--color-primary) 3%, var(--color-card-bg)); }
.shared-qa-card__question > span,.shared-qa-card__answer > span { display: block; margin-bottom: 10px; color: var(--color-text-tertiary); font-size: 12px; font-weight: 700; text-transform: uppercase; }
.shared-qa-card__question > p { margin: 0; color: var(--color-text-title); font-size: 16px; font-weight: 600; line-height: 1.65; white-space: pre-wrap; }
.shared-qa-card__answer :deep(.copilot-markdown) { color: var(--color-text-primary); font-size: 15px; line-height: 1.75; }
.shared-qa-card footer { padding: 12px 28px 18px; color: var(--color-text-tertiary); font-size: 12px; text-align: center; }
.shared-qa-files { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 14px; }
.shared-qa-files button { display: inline-flex; max-width: 100%; min-height: 42px; align-items: center; gap: 8px; padding: 7px 11px; border: 1px solid var(--color-border); border-radius: 9px; background: var(--color-card-bg); color: var(--color-text-secondary); cursor: pointer; }
.shared-qa-files button span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.shared-qa-files button:hover { border-color: color-mix(in srgb, var(--color-primary) 40%, var(--color-border)); color: var(--color-primary); }
.shared-qa-page__back:focus-visible,.shared-qa-files button:focus-visible { outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent); outline-offset: 2px; }
.shared-qa-files--output { margin-top: 20px; }
@media (max-width: 767.98px) {
  .shared-qa-page { padding: 14px 10px 32px; }
  .shared-qa-card__header { grid-template-columns: auto minmax(0, 1fr); padding: 18px; }
  .shared-qa-card__pdf { grid-column: 1 / -1; width: 100%; min-height: 44px; }
  .shared-qa-card__question,.shared-qa-card__answer { padding: 20px 18px; }
  .shared-qa-files button { width: 100%; min-height: 48px; }
}
</style>

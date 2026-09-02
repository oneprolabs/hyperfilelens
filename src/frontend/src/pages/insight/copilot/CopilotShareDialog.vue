<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Bot, Check, Copy, Share2 } from 'lucide-vue-next'
import DangerConfirmDialog from '../../../components/DangerConfirmDialog.vue'
import { apiErrorMessage } from '../../../lib/api'
import { copyTextToClipboard } from '../../../lib/clipboard'
import {
  createCopilotShare,
  fetchCopilotShareCandidate,
  revokeCopilotShare,
  updateCopilotShare,
  type LensCopilotShareCandidate,
  type LensSharedQA,
} from '../../../lib/lensApi'
import type { SessionRow } from './sessionOrdering'

const props = defineProps<{
  modelValue: boolean
  session: SessionRow | null
}>()

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
  closed: []
}>()

const { t } = useI18n()
const loading = ref(false)
const saving = ref(false)
const candidate = ref<LensCopilotShareCandidate | null>(null)
const share = ref<LensSharedQA | null>(null)
const title = ref('')
const copied = ref(false)
const stopConfirmOpen = ref(false)
let candidateLoadGeneration = 0
let dialogGeneration = 0
let copyFeedbackTimer: ReturnType<typeof setTimeout> | undefined

const open = computed({
  get: () => props.modelValue,
  set: (value: boolean) => emit('update:modelValue', value),
})

function defaultTitle(question?: string) {
  return (question || '').replace(/\s+/g, ' ').trim().slice(0, 80)
}

const shareUrl = computed(() => {
  if (!share.value?.share_path || typeof window === 'undefined') return ''
  return new URL(share.value.share_path, window.location.origin).toString()
})

const titleDirty = computed(
  () => Boolean(share.value) && title.value.trim() !== (share.value?.title || ''),
)
const nativeShareAvailable = computed(() => (
  typeof navigator !== 'undefined' && typeof navigator.share === 'function'
))

const primaryLabel = computed(() => {
  if (!share.value) return t('insight.copilot.shareCreateLink')
  return titleDirty.value
    ? t('insight.copilot.shareSaveTitle')
    : t('insight.copilot.shareDone')
})

async function loadCandidate() {
  const sessionId = props.session?.id
  if (!sessionId) return
  const generation = ++candidateLoadGeneration
  loading.value = true
  candidate.value = null
  share.value = null
  title.value = ''
  try {
    const result = await fetchCopilotShareCandidate(sessionId)
    if (
      generation !== candidateLoadGeneration
      || props.session?.id !== sessionId
      || !props.modelValue
    ) return
    candidate.value = result
    share.value = result.share || null
    title.value = share.value?.title || defaultTitle(result.question)
  } catch (error) {
    if (generation !== candidateLoadGeneration || !props.modelValue) return
    ElMessage.error({
      message: apiErrorMessage(error, t('insight.copilot.shareFailed')),
      grouping: true,
    })
    open.value = false
  } finally {
    if (generation === candidateLoadGeneration) loading.value = false
  }
}

watch(
  () => [props.modelValue, props.session?.id] as const,
  ([isOpen]) => {
    dialogGeneration += 1
    saving.value = false
    stopConfirmOpen.value = false
    copied.value = false
    if (isOpen) {
      void loadCandidate()
      return
    }
    candidateLoadGeneration += 1
    loading.value = false
  },
)

async function primaryAction() {
  if (!props.session || saving.value || !title.value.trim()) return
  if (share.value && !titleDirty.value) {
    open.value = false
    return
  }
  const sessionId = props.session.id
  const generation = dialogGeneration
  const updating = Boolean(share.value)
  saving.value = true
  try {
    const result = share.value
      ? await updateCopilotShare(sessionId, share.value.uuid || '', title.value.trim())
      : await createCopilotShare(sessionId, title.value.trim())
    if (
      generation !== dialogGeneration
      || props.session?.id !== sessionId
      || !props.modelValue
    ) return
    share.value = result
    title.value = result.title || ''
    ElMessage.success({
      message: t(
        updating
          ? 'insight.copilot.shareUpdated'
          : 'insight.copilot.shareCreated',
      ),
      grouping: true,
    })
  } catch (error) {
    if (
      generation !== dialogGeneration
      || props.session?.id !== sessionId
      || !props.modelValue
    ) return
    ElMessage.error({
      message: apiErrorMessage(error, t('insight.copilot.shareFailed')),
      grouping: true,
    })
  } finally {
    if (generation === dialogGeneration) saving.value = false
  }
}

async function copyLink() {
  if (!shareUrl.value) return
  try {
    await copyTextToClipboard(shareUrl.value)
    copied.value = true
    if (copyFeedbackTimer) clearTimeout(copyFeedbackTimer)
    copyFeedbackTimer = setTimeout(() => {
      copied.value = false
      copyFeedbackTimer = undefined
    }, 2000)
    ElMessage.success({ message: t('insight.copilot.shareCopied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('insight.copilot.shareCopyFailed'), grouping: true })
  }
}

async function nativeShare() {
  if (!shareUrl.value || !nativeShareAvailable.value) return
  try {
    await navigator.share({
      title: title.value.trim() || defaultTitle(candidate.value?.question),
      text: t('insight.copilot.shareInvitation', {
        name: props.session?.assistant_name || t('insight.copilot.shareGenericAssistant'),
      }),
      url: shareUrl.value,
    })
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') return
    ElMessage.error({ message: t('insight.copilot.shareFailed'), grouping: true })
  }
}

async function stopSharing() {
  if (!props.session || !share.value?.uuid || saving.value) return
  const sessionId = props.session.id
  const shareUuid = share.value.uuid
  const generation = dialogGeneration
  saving.value = true
  try {
    await revokeCopilotShare(sessionId, shareUuid)
    if (
      generation !== dialogGeneration
      || props.session?.id !== sessionId
      || !props.modelValue
    ) return
    ElMessage.success({ message: t('insight.copilot.shareRevoked'), grouping: true })
    stopConfirmOpen.value = false
    open.value = false
  } catch (error) {
    if (
      generation !== dialogGeneration
      || props.session?.id !== sessionId
      || !props.modelValue
    ) return
    ElMessage.error({
      message: apiErrorMessage(error, t('insight.copilot.shareFailed')),
      grouping: true,
    })
  } finally {
    if (generation === dialogGeneration) saving.value = false
  }
}

onBeforeUnmount(() => {
  if (copyFeedbackTimer) clearTimeout(copyFeedbackTimer)
})
</script>

<template>
  <ElDialog
    v-model="open"
    class="copilot-share-dialog"
    width="min(640px, calc(100vw - 32px))"
    :title="t('insight.copilot.shareTitle')"
    append-to-body
    destroy-on-close
    @closed="emit('closed')"
  >
    <div
      v-loading="loading"
      class="copilot-share-dialog__body"
    >
      <div class="copilot-share-dialog__intro">
        <Bot
          :size="18"
          aria-hidden="true"
        />
        <div>
          <strong>{{ t('insight.copilot.shareAgentTitle') }}</strong>
          <p>
            {{ t('insight.copilot.shareAgentDescription', {
              name: session?.assistant_name || t('insight.copilot.shareGenericAssistant'),
            }) }}
          </p>
        </div>
      </div>

      <p class="copilot-share-dialog__warning">
        {{ t('insight.copilot.shareWarning') }}
      </p>

      <template v-if="candidate?.shareable">
        <label
          class="copilot-share-dialog__label"
          for="copilot-share-title"
        >
          {{ t('insight.copilot.shareTitleLabel') }}
        </label>
        <ElInput
          id="copilot-share-title"
          v-model="title"
          maxlength="200"
        />

        <div
          v-if="candidate.answer"
          class="copilot-share-dialog__preview"
        >
          <span>{{ t('insight.copilot.sharePreview') }}</span>
          <p>{{ candidate.answer }}</p>
        </div>

        <div
          v-if="share"
          class="copilot-share-dialog__link-block"
        >
          <span class="copilot-share-dialog__label">
            {{ t('insight.copilot.shareLinkLabel') }}
          </span>
          <div
            class="copilot-share-dialog__link-row"
            role="group"
            :aria-label="t('insight.copilot.shareLinkLabel')"
          >
            <a
              :href="shareUrl"
              target="_blank"
              rel="noopener"
              :title="shareUrl"
            >
              <span>{{ shareUrl }}</span>
            </a>
            <button
              type="button"
              :aria-label="t('insight.copilot.shareCopyLink')"
              :title="t('insight.copilot.shareCopyLink')"
              @click="copyLink"
            >
              <component
                :is="copied ? Check : Copy"
                :size="16"
                aria-hidden="true"
              />
              <span aria-live="polite">
                {{ t(copied ? 'insight.copilot.shareCopied' : 'insight.copilot.shareCopyLink') }}
              </span>
            </button>
          </div>
          <p>{{ t('insight.copilot.shareOrgOnly') }}</p>
          <ElButton
            v-if="nativeShareAvailable"
            class="w-full"
            type="primary"
            plain
            @click="nativeShare"
          >
            <Share2
              :size="16"
              aria-hidden="true"
            />
            {{ t('insight.copilot.shareAction') }}
          </ElButton>
        </div>
      </template>

      <ElEmpty
        v-else-if="!loading"
        :description="t('insight.copilot.shareUnavailable')"
        :image-size="72"
      />
    </div>

    <template #footer>
      <div class="copilot-share-dialog__footer">
        <ElButton
          v-if="share"
          type="danger"
          plain
          :disabled="saving"
          @click="stopConfirmOpen = true"
        >
          {{ t('insight.copilot.shareStop') }}
        </ElButton>
        <span class="copilot-share-dialog__footer-spacer" />
        <ElButton
          v-if="!share"
          @click="open = false"
        >
          {{ t('insight.copilot.btnCancel') }}
        </ElButton>
        <ElButton
          v-if="candidate?.shareable"
          type="primary"
          :loading="saving"
          :disabled="!title.trim()"
          @click="primaryAction"
        >
          {{ primaryLabel }}
        </ElButton>
      </div>
    </template>
  </ElDialog>

  <DangerConfirmDialog
    v-model="stopConfirmOpen"
    :title="t('insight.copilot.shareStopConfirmTitle')"
    :message="t('insight.copilot.shareStopConfirmMessage')"
    :cancel-text="t('insight.copilot.btnCancel')"
    :confirm-text="t('insight.copilot.shareStop')"
    :loading="saving"
    @confirm="stopSharing"
  />
</template>

<style scoped>
.copilot-share-dialog__body { min-height: 180px; max-height: min(68vh, 660px); overflow-y: auto; padding-right: 2px; }
.copilot-share-dialog__intro { display: flex; align-items: flex-start; gap: 12px; padding: 14px; border: 1px solid color-mix(in srgb, var(--color-primary) 22%, var(--color-border)); border-radius: 10px; background: color-mix(in srgb, var(--color-primary) 6%, var(--color-card-bg)); color: var(--color-primary); }
.copilot-share-dialog__intro strong { display: block; color: var(--color-text-title); font-size: 14px; }
.copilot-share-dialog__intro p { margin: 4px 0 0; color: var(--color-text-secondary); font-size: 12px; line-height: 1.55; }
.copilot-share-dialog__warning { margin: 12px 0 18px; padding: 10px 12px; border-radius: 8px; background: var(--el-color-warning-light-9); color: var(--el-color-warning-dark-2); font-size: 12px; line-height: 1.5; }
.copilot-share-dialog__label { display: block; margin-bottom: 6px; color: var(--color-text-secondary); font-size: 12px; font-weight: 600; }
.copilot-share-dialog__preview { margin-top: 16px; }
.copilot-share-dialog__preview > span { color: var(--color-text-secondary); font-size: 12px; font-weight: 600; }
.copilot-share-dialog__preview p { display: -webkit-box; margin: 6px 0 0; padding: 11px 12px; overflow: hidden; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-grey-2); color: var(--color-text-secondary); font-size: 12px; line-height: 1.55; -webkit-box-orient: vertical; -webkit-line-clamp: 4; }
.copilot-share-dialog__link-block { margin-top: 18px; }
.copilot-share-dialog__link-row { display: flex; min-height: 44px; align-items: stretch; overflow: hidden; border: 1px solid var(--color-border); border-radius: 8px; background: var(--color-card-bg); }
.copilot-share-dialog__link-row:focus-within { border-color: color-mix(in srgb, var(--color-primary) 55%, var(--color-border)); box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary) 14%, transparent); }
.copilot-share-dialog__link-row a { display: flex; min-width: 0; flex: 1; align-items: center; overflow: hidden; padding: 0 12px; color: var(--color-primary); font-family: ui-monospace, monospace; font-size: 12px; text-decoration: none; }
.copilot-share-dialog__link-row a span { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.copilot-share-dialog__link-row button { display: inline-flex; min-width: 112px; min-height: 44px; flex: 0 0 auto; align-items: center; justify-content: center; gap: 7px; padding: 0 12px; border: 0; border-left: 1px solid var(--color-border); background: var(--color-grey-2); color: var(--color-text-secondary); cursor: pointer; font: inherit; font-size: 12px; font-weight: 600; }
.copilot-share-dialog__link-row button:hover { background: color-mix(in srgb, var(--color-primary) 7%, var(--color-grey-2)); color: var(--color-primary); }
.copilot-share-dialog__link-row button:focus-visible { outline: 2px solid color-mix(in srgb, var(--color-primary) 55%, transparent); outline-offset: 2px; }
.copilot-share-dialog__link-block > p { margin: 9px 0 12px; color: var(--color-text-tertiary); font-size: 12px; line-height: 1.5; }
.copilot-share-dialog__footer { display: flex; align-items: center; gap: 8px; width: 100%; }
.copilot-share-dialog__footer-spacer { flex: 1; }
@media (max-width: 767.98px) {
  .copilot-share-dialog__body { max-height: calc(100vh - 210px); }
  .copilot-share-dialog__link-row button { min-width: 44px; width: 44px; padding: 0; }
  .copilot-share-dialog__link-row button span { display: none; }
  .copilot-share-dialog__footer { display: grid; grid-template-columns: 1fr 1fr; }
  .copilot-share-dialog__footer-spacer { display: none; }
  .copilot-share-dialog__footer :deep(.el-button) { width: 100%; min-height: 44px; margin: 0; }
}
</style>

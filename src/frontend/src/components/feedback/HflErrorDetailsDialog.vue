<script setup lang="ts">
import { computed, ref } from 'vue'
import { AlertTriangle, Check, ChevronDown, Copy, X } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'
import { copyTextToClipboard } from '../../lib/clipboard'
import {
  closeErrorDetails,
  errorDetailsCopyText,
  errorDetailsState,
  safeErrorDetailText,
} from '../../lib/errors/details'

const { t } = useI18n()
const copied = ref(false)
const details = computed(() => errorDetailsState.current)
const rawText = computed(() => safeErrorDetailText(details.value?.rawDetail))

async function copyDetails() {
  if (!details.value) return
  await copyTextToClipboard(errorDetailsCopyText(details.value))
  copied.value = true
  window.setTimeout(() => { copied.value = false }, 1600)
}
</script>

<template>
  <ElDialog
    :model-value="Boolean(details)"
    width="min(600px, calc(100vw - 32px))"
    class="hfl-error-details"
    modal-class="hfl-error-details-overlay"
    append-to-body
    destroy-on-close
    :show-close="false"
    @update:model-value="(open: boolean) => { if (!open) closeErrorDetails() }"
  >
    <template
      v-if="details"
      #header
    >
      <div class="hfl-error-details__header">
        <span
          class="hfl-error-details__icon"
          aria-hidden="true"
        ><AlertTriangle :size="24" /></span>
        <div class="hfl-error-details__heading">
          <div class="hfl-error-details__title-line">
            <h2>{{ details.title }}</h2>
            <span
              v-if="details.errorCode"
              class="hfl-error-details__code"
            >{{ details.errorCode }}</span>
          </div>
          <p>{{ details.summary }}</p>
        </div>
        <button
          type="button"
          class="hfl-error-details__close"
          :aria-label="t('common.close')"
          @click="closeErrorDetails"
        >
          <X :size="17" />
        </button>
      </div>
    </template>

    <template v-if="details">
      <section
        v-if="details.issue"
        class="hfl-error-details__section"
      >
        <h3>{{ t('feedback.errorDetails.issue') }}</h3>
        <div class="hfl-error-details__issue">
          {{ details.issue }}
        </div>
      </section>

      <section
        v-if="details.reasons?.length"
        class="hfl-error-details__section"
      >
        <h3>{{ t('feedback.errorDetails.reasons') }}</h3>
        <ol class="hfl-error-details__list">
          <li
            v-for="reason in details.reasons"
            :key="reason"
          >
            {{ reason }}
          </li>
        </ol>
      </section>

      <section
        v-if="details.resolutions?.length"
        class="hfl-error-details__section"
      >
        <h3>{{ t('feedback.errorDetails.resolutions') }}</h3>
        <ul class="hfl-error-details__list hfl-error-details__list--resolve">
          <li
            v-for="resolution in details.resolutions"
            :key="resolution"
          >
            {{ resolution }}
          </li>
        </ul>
      </section>

      <section
        v-if="details.traceId || rawText"
        class="hfl-error-details__section hfl-error-details__technical-desktop"
      >
        <h3>{{ t('feedback.errorDetails.technical') }}</h3>
        <div
          v-if="details.traceId"
          class="hfl-error-details__trace"
        >
          <span>{{ t('errors.generic.traceId') }}</span>
          <code>{{ details.traceId }}</code>
        </div>
        <pre
          v-if="rawText"
          class="hfl-error-details__raw"
        >{{ rawText }}</pre>
      </section>

      <details
        v-if="details.traceId || rawText"
        class="hfl-error-details__section hfl-error-details__technical-mobile"
      >
        <summary class="hfl-error-details__technical-summary">
          <span>{{ t('feedback.errorDetails.technical') }}</span>
          <ChevronDown
            :size="17"
            aria-hidden="true"
          />
        </summary>
        <div class="hfl-error-details__technical-content">
          <div
            v-if="details.traceId"
            class="hfl-error-details__trace"
          >
            <span>{{ t('errors.generic.traceId') }}</span>
            <code>{{ details.traceId }}</code>
          </div>
          <pre
            v-if="rawText"
            class="hfl-error-details__raw"
          >{{ rawText }}</pre>
        </div>
      </details>
    </template>

    <template
      v-if="details"
      #footer
    >
      <div class="hfl-error-details__footer">
        <button
          type="button"
          class="hfl-error-details__copy"
          @click="copyDetails"
        >
          <Check
            v-if="copied"
            :size="15"
          />
          <Copy
            v-else
            :size="15"
          />
          {{ copied ? t('feedback.toast.copied') : t('feedback.errorDetails.copy') }}
        </button>
        <ElButton
          type="primary"
          @click="closeErrorDetails"
        >
          {{ t('common.confirm') }}
        </ElButton>
      </div>
    </template>
  </ElDialog>
</template>

<style>
.hfl-error-details.el-dialog { padding: 0; overflow: hidden; border-radius: 18px; }
.hfl-error-details .el-dialog__header { padding: 0; margin: 0; }
.hfl-error-details .el-dialog__body { max-height: min(62vh, 640px); padding: 2px 26px 10px; overflow-y: auto; }
.hfl-error-details .el-dialog__footer { display: block; width: 100%; padding: 0; margin: 0; }
.hfl-error-details__header { position: relative; display: flex; gap: 15px; padding: 24px 54px 18px 26px; }
.hfl-error-details__icon { display: inline-flex; flex: 0 0 auto; width: 46px; height: 46px; align-items: center; justify-content: center; color: var(--color-error); background: var(--color-error-light); border-radius: 13px; }
.hfl-error-details__heading { min-width: 0; padding-top: 1px; }
.hfl-error-details__title-line { display: flex; flex-wrap: wrap; gap: 9px; align-items: center; }
.hfl-error-details__title-line h2 { margin: 0; color: var(--color-text-title); font-size: 18px; font-weight: 700; line-height: 25px; }
.hfl-error-details__heading p { margin: 4px 0 0; color: var(--color-text-secondary); font-size: 13px; line-height: 20px; }
.hfl-error-details__code { padding: 3px 7px; color: var(--color-error); background: var(--color-error-light); border-radius: 6px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11px; overflow-wrap: anywhere; }
.hfl-error-details__close { position: absolute; top: 18px; right: 18px; display: inline-flex; width: 30px; height: 30px; padding: 0; align-items: center; justify-content: center; color: var(--color-text-tertiary); background: transparent; border: 0; border-radius: 8px; cursor: pointer; }
.hfl-error-details__close:hover { color: var(--color-text-primary); background: var(--color-grey-2); }
.hfl-error-details__section + .hfl-error-details__section { margin-top: 20px; }
.hfl-error-details__section h3 { margin: 0 0 8px; color: var(--color-text-tertiary); font-size: 11px; font-weight: 700; letter-spacing: .09em; text-transform: uppercase; }
.hfl-error-details__issue { padding: 12px 14px; color: var(--color-error); background: color-mix(in srgb, var(--color-error-light) 76%, transparent); border: 1px solid color-mix(in srgb, var(--color-error) 20%, transparent); border-radius: 11px; font-size: 13px; line-height: 1.55; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; }
.hfl-error-details__list { display: grid; gap: 10px; padding: 0; margin: 0; list-style: none; counter-reset: detail-item; }
.hfl-error-details__list li { position: relative; min-height: 20px; padding-left: 31px; color: var(--color-text-primary); font-size: 13px; line-height: 20px; counter-increment: detail-item; }
.hfl-error-details__list li::before { position: absolute; top: 0; left: 0; display: inline-flex; width: 20px; height: 20px; align-items: center; justify-content: center; content: counter(detail-item); color: var(--color-text-primary); background: var(--color-grey-2); border-radius: 6px; font-size: 11px; font-weight: 700; }
.hfl-error-details__list--resolve li::before { content: '✓'; color: var(--color-success); background: var(--color-success-light); border-radius: 50%; }
.hfl-error-details__trace { display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 8px; color: var(--color-text-secondary); font-size: 12px; }
.hfl-error-details__trace code { color: var(--color-text-title); user-select: text; }
.hfl-error-details__raw { max-height: 220px; padding: 12px 14px; margin: 0; overflow: auto; color: var(--color-text-primary); background: var(--color-grey-2); border: 1px solid var(--color-border-light); border-radius: 10px; font: 12px/1.55 ui-monospace, SFMono-Regular, Menlo, monospace; white-space: pre-wrap; overflow-wrap: anywhere; user-select: text; }
.hfl-error-details__footer { display: flex; width: 100%; box-sizing: border-box; align-items: center; justify-content: space-between; gap: 12px; padding: 14px 26px; background: var(--color-grey-1); border-top: 1px solid var(--color-border-light); }
.hfl-error-details__copy { display: inline-flex; gap: 7px; padding: 8px 0; align-items: center; color: var(--color-text-primary); background: transparent; border: 0; font-size: 12.5px; font-weight: 600; cursor: pointer; }
.hfl-error-details__copy:hover { color: var(--color-primary); }
.hfl-error-details__technical-mobile { display: none; }

@media (max-width: 640px) {
  .hfl-error-details-overlay .el-overlay-dialog {
    display: flex;
    align-items: flex-end;
    padding-top: calc(var(--app-safe-top) + 8px);
  }

  .hfl-error-details.el-dialog {
    display: flex;
    width: 100% !important;
    max-width: none;
    max-height: calc(var(--app-viewport-height) - var(--app-safe-top) - 8px);
    margin: 0;
    flex-direction: column;
    border-radius: 18px 18px 0 0;
  }

  .hfl-error-details .el-dialog__header {
    flex: 0 0 auto;
  }

  .hfl-error-details .el-dialog__body {
    min-height: 0;
    max-height: none;
    padding: 2px 16px 12px;
    flex: 1 1 auto;
  }

  .hfl-error-details .el-dialog__footer {
    flex: 0 0 auto;
  }

  .hfl-error-details__header {
    gap: 12px;
    padding: 16px 52px 14px 16px;
  }

  .hfl-error-details__icon {
    width: 36px;
    height: 36px;
    border-radius: 10px;
  }

  .hfl-error-details__title-line h2 {
    font-size: 16px;
    line-height: 22px;
  }

  .hfl-error-details__heading p {
    margin-top: 2px;
  }

  .hfl-error-details__close {
    top: 8px;
    right: 8px;
    width: 44px;
    height: 44px;
  }

  .hfl-error-details__section + .hfl-error-details__section {
    margin-top: 16px;
  }

  .hfl-error-details__technical-desktop {
    display: none;
  }

  .hfl-error-details__technical-mobile {
    display: block;
  }

  .hfl-error-details__technical-summary {
    display: flex;
    min-height: 44px;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    color: var(--color-text-tertiary);
    cursor: pointer;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: .09em;
    list-style: none;
    text-transform: uppercase;
  }

  .hfl-error-details__technical-summary::-webkit-details-marker {
    display: none;
  }

  .hfl-error-details__technical-summary svg {
    flex: 0 0 auto;
    transition: transform var(--transition-fast);
  }

  .hfl-error-details__technical-mobile[open] .hfl-error-details__technical-summary svg {
    transform: rotate(180deg);
  }

  .hfl-error-details__technical-content {
    padding-bottom: 4px;
  }

  .hfl-error-details__footer {
    padding: 10px 16px calc(10px + var(--app-safe-bottom));
  }

  .hfl-error-details__copy,
  .hfl-error-details__footer .el-button {
    min-height: 44px;
  }
}
</style>

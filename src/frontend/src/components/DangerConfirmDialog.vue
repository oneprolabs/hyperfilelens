<script setup lang="ts">
/**
 *
 *
 */
import { computed, ref, watch } from 'vue'
import { AlertTriangle, LoaderCircle, ShieldAlert, X } from 'lucide-vue-next'
import { ElButton, ElDialog } from 'element-plus'
import ExactKeywordConfirmInput from './ExactKeywordConfirmInput.vue'

export type DangerLevel = 'low' | 'medium' | 'high'

export type DangerConfirmMode = 'single' | 'keyword'

export type DangerConfirmStatusTone = 'success' | 'warning' | 'danger' | 'info' | 'neutral'

export interface DangerConfirmItem {
  key?: string | number
  name: string
  description?: string
  status?: { label: string; tone?: DangerConfirmStatusTone }
  hint?: string
  warning?: string
}

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    subtitle?: string
    level?: DangerLevel
    message?: string
    warning?: string
    items?: DangerConfirmItem[]
    maxItemRows?: number
    overflowLabel?: string
    itemsHeading?: string
    itemNameLabel?: string
    itemStatusLabel?: string
    itemDetailsLabel?: string
    confirmMode?: DangerConfirmMode
    confirmKeyword?: string
    confirmKeywordHint?: string
    confirmKeywordPlaceholder?: string
    irreversibleHint?: string
    irreversibleTone?: 'danger' | 'neutral'
    cancelText?: string
    confirmText?: string
    loading?: boolean
    confirmDisabled?: boolean
    pending?: boolean
    pendingText?: string
    errorText?: string
    width?: string
    beforeClose?: () => boolean | Promise<boolean>
  }>(),
  {
    subtitle: '',
    level: 'high',
    message: '',
    warning: '',
    items: undefined,
    maxItemRows: 5,
    overflowLabel: '',
    itemsHeading: '',
    itemNameLabel: undefined,
    itemStatusLabel: undefined,
    itemDetailsLabel: undefined,
    confirmMode: 'single',
    confirmKeyword: 'DELETE',
    confirmKeywordHint: '',
    confirmKeywordPlaceholder: '',
    irreversibleHint: '',
    irreversibleTone: 'danger',
    cancelText: undefined,
    confirmText: undefined,
    loading: false,
    pendingText: undefined,
    errorText: '',
    width: undefined,
    beforeClose: undefined,
  },
)

const emit = defineEmits<{
  (e: 'update:modelValue', v: boolean): void
  (e: 'confirm'): void
  (e: 'cancel'): void
}>()

const visible = computed({
  get: () => props.modelValue,
  set: (v: boolean) => emit('update:modelValue', v),
})
const confirmInput = ref('')

const accentClass = computed(() => `hfl-danger-confirm--${props.level}`)
const Icon = computed(() => (props.level === 'high' ? ShieldAlert : AlertTriangle))
const confirmButtonType = computed<'primary' | 'warning' | 'danger'>(() => {
  if (props.level === 'high') return 'danger'
  if (props.level === 'medium') return 'warning'
  return 'primary'
})

const shownItems = computed(() => {
  if (!props.items?.length) return [] as DangerConfirmItem[]
  return props.items
})
const hasItems = computed(() => shownItems.value.length > 0)
const usesItemLayout = computed(() => props.items !== undefined)
const dialogWidth = computed(() => (
  props.width
  ?? (usesItemLayout.value
    ? 'min(600px, calc(100vw - 32px))'
    : 'min(480px, calc(100vw - 32px))')
))
const needsKeyword = computed(() => props.confirmMode === 'keyword')
const keywordMatched = computed(() => (
  !needsKeyword.value
  || confirmInput.value === props.confirmKeyword
))
const confirmDisabled = computed(() => (
  props.pending
  || Boolean(props.errorText)
  || Boolean(props.confirmDisabled)
  || !keywordMatched.value
))

watch(
  () => [props.modelValue, props.confirmKeyword, props.confirmMode],
  () => {
    confirmInput.value = ''
  },
)

function statusClass(tone?: DangerConfirmStatusTone) {
  switch (tone) {
    case 'success':
      return 'hfl-danger-confirm__status--success'
    case 'warning':
      return 'hfl-danger-confirm__status--warning'
    case 'danger':
      return 'hfl-danger-confirm__status--danger'
    case 'info':
      return 'hfl-danger-confirm__status--info'
    default:
      return 'hfl-danger-confirm__status--neutral'
  }
}

function close() {
  if (props.loading) return
  emit('cancel')
  visible.value = false
}

async function onPrimary() {
  if (props.loading || props.pending || props.errorText) return
  if (!keywordMatched.value) return
  emit('confirm')
}

async function onDialogBeforeClose(done: () => void) {
  if (props.loading) return
  if (props.beforeClose) {
    const allow = await props.beforeClose()
    if (!allow) return
  }
  emit('cancel')
  done()
}
</script>

<template>
  <ElDialog
    v-model="visible"
    :width="dialogWidth"
    :close-on-click-modal="false"
    :close-on-press-escape="!loading"
    :show-close="false"
    :align-center="true"
    :modal-class="`hfl-danger-confirm ${accentClass}`"
    :before-close="onDialogBeforeClose"
  >
    <template #header>
      <div class="hfl-danger-confirm__header">
        <div
          class="hfl-danger-confirm__icon"
          :class="accentClass"
          aria-hidden="true"
        >
          <component
            :is="Icon"
            :size="20"
          />
        </div>
        <div class="hfl-danger-confirm__titles">
          <h2 class="hfl-danger-confirm__title">
            {{ title }}
          </h2>
          <p
            v-if="subtitle"
            class="hfl-danger-confirm__subtitle"
          >
            {{ subtitle }}
          </p>
        </div>
        <button
          type="button"
          class="hfl-danger-confirm__close"
          :disabled="loading"
          :aria-label="cancelText ?? 'Close'"
          @click="close"
        >
          <X :size="16" />
        </button>
      </div>
    </template>

    <div class="hfl-danger-confirm__body">
      <div
        v-if="pending"
        class="hfl-danger-confirm__pending"
      >
        <LoaderCircle
          :size="18"
          class="hfl-danger-confirm__pending-icon"
        />
        <span>{{ pendingText ?? 'Checking dependencies...' }}</span>
      </div>

      <div
        v-else-if="errorText"
        class="hfl-danger-confirm__error"
      >
        <AlertTriangle :size="16" />
        <span>{{ errorText }}</span>
      </div>

      <template v-else>
        <p
          v-if="message"
          class="hfl-danger-confirm__message"
        >
          {{ message }}
        </p>

        <div
          v-if="warning"
          class="hfl-danger-confirm__warning"
        >
          <AlertTriangle :size="16" />
          <span>{{ warning }}</span>
        </div>

        <section
          v-if="hasItems"
          class="hfl-danger-confirm__items"
        >
          <header
            v-if="itemsHeading"
            class="hfl-danger-confirm__items-heading"
          >
            {{ itemsHeading }}
          </header>
          <div class="hfl-danger-confirm__item-table-scroll">
            <table class="hyper-table hfl-danger-confirm__item-table">
              <colgroup>
                <col class="hfl-danger-confirm__item-col-name">
                <col class="hfl-danger-confirm__item-col-status">
                <col class="hfl-danger-confirm__item-col-details">
              </colgroup>
              <thead>
                <tr>
                  <th scope="col">
                    {{ itemNameLabel ?? 'Name' }}
                  </th>
                  <th scope="col">
                    {{ itemStatusLabel ?? 'Status' }}
                  </th>
                  <th scope="col">
                    {{ itemDetailsLabel ?? 'Details' }}
                  </th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="(item, idx) in shownItems"
                  :key="item.key ?? idx"
                >
                  <td
                    class="hfl-danger-confirm__item-name-cell"
                    :class="{ 'hfl-danger-confirm__item-name-cell--full': !item.status }"
                  >
                    <span
                      class="hfl-danger-confirm__item-name"
                      :title="item.name"
                    >{{ item.name }}</span>
                  </td>
                  <td class="hfl-danger-confirm__item-status-cell">
                    <span
                      v-if="item.status"
                      class="hfl-danger-confirm__status"
                      :class="statusClass(item.status.tone)"
                      :title="item.status.label"
                    >{{ item.status.label }}</span>
                  </td>
                  <td
                    class="hfl-danger-confirm__item-details-cell"
                    :class="{
                      'hfl-danger-confirm__item-details-cell--empty':
                        !item.description && !item.hint && !item.warning,
                    }"
                  >
                    <div class="hfl-danger-confirm__item-details">
                      <span
                        v-if="item.description || item.hint"
                        class="hfl-danger-confirm__item-desc"
                        :title="item.description || item.hint"
                      >{{ item.description || item.hint }}</span>
                      <span
                        v-if="item.warning"
                        class="hfl-danger-confirm__item-warning"
                      >
                        <AlertTriangle
                          :size="12"
                          aria-hidden="true"
                        />
                        <span>{{ item.warning }}</span>
                      </span>
                      <span
                        v-if="!item.description && !item.hint && !item.warning"
                        class="hfl-danger-confirm__item-desc hfl-empty-mark"
                      >—</span>
                    </div>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <slot />

        <p
          v-if="irreversibleHint"
          class="hfl-danger-confirm__irreversible"
          :class="`hfl-danger-confirm__irreversible--${irreversibleTone}`"
        >
          {{ irreversibleHint }}
        </p>

        <section
          v-if="needsKeyword"
          class="hfl-danger-confirm__keyword"
        >
          <ExactKeywordConfirmInput
            v-model="confirmInput"
            :keyword="confirmKeyword"
            :hint="confirmKeywordHint"
            :placeholder="confirmKeywordPlaceholder"
            :disabled="loading"
            @confirm="onPrimary"
          />
        </section>
      </template>

      <slot name="extra" />
    </div>

    <template #footer>
      <div class="hfl-danger-confirm__footer">
        <slot
          name="footer"
          :loading="loading"
          :disabled="confirmDisabled"
          :keyword-matched="keywordMatched"
          :confirm="onPrimary"
          :cancel="close"
        >
          <ElButton
            :disabled="loading"
            @click="close"
          >
            {{ cancelText ?? 'Cancel' }}
          </ElButton>
          <ElButton
            :type="confirmButtonType"
            :loading="loading"
            :disabled="confirmDisabled"
            @click="onPrimary"
          >
            {{ confirmText ?? 'Confirm' }}
          </ElButton>
        </slot>
      </div>
    </template>
  </ElDialog>
</template>

<style scoped>
.hfl-danger-confirm {
  --dcf-pad-x: 20px;
  --dcf-pad-y-header: 14px;
  --dcf-pad-y-body: 16px;
  --dcf-pad-y-footer: 12px;
  --dcf-gap: 14px;
  --dcf-radius: 10px;
}

.hfl-danger-confirm :deep(.el-dialog__header) {
  padding: 0;
  margin: 0;
}
.hfl-danger-confirm :deep(.el-dialog__body) {
  padding: 0;
}
.hfl-danger-confirm :deep(.el-dialog__footer) {
  padding: 0;
}
.hfl-danger-confirm :deep(.el-dialog) {
  border-radius: var(--dcf-radius);
  overflow: hidden;
  max-width: calc(100vw - 32px);
}

/* ===== header ===== */
.hfl-danger-confirm__header {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: var(--dcf-pad-y-header) var(--dcf-pad-x);
}
.hfl-danger-confirm__icon {
  flex: 0 0 34px;
  width: 34px;
  height: 34px;
  border-radius: 8px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: #fff;
}
.hfl-danger-confirm__icon.hfl-danger-confirm--low {
  background: #2563eb;
}
.hfl-danger-confirm__icon.hfl-danger-confirm--medium {
  background: #f59e0b;
}
.hfl-danger-confirm__icon.hfl-danger-confirm--high {
  background: #dc2626;
}
.hfl-danger-confirm__titles {
  flex: 1 1 auto;
  min-width: 0;
}
.hfl-danger-confirm__title {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
  margin: 0;
  color: var(--el-text-color-primary, #111827);
}
.hfl-danger-confirm__subtitle {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--el-text-color-secondary, #6b7280);
}
.hfl-danger-confirm__close {
  appearance: none;
  background: transparent;
  border: 0;
  padding: 4px;
  margin: 0;
  border-radius: 6px;
  cursor: pointer;
  color: var(--el-text-color-secondary, #6b7280);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  transition: background 120ms ease, color 120ms ease;
}
.hfl-danger-confirm__close:hover {
  background: rgba(0, 0, 0, 0.06);
  color: var(--el-text-color-primary, #111827);
}
.hfl-danger-confirm__close:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

/* ===== body ===== */
.hfl-danger-confirm__body {
  padding: 18px var(--dcf-pad-x) var(--dcf-pad-y-body);
  display: flex;
  flex-direction: column;
  gap: var(--dcf-gap);
  font-size: 14px;
  line-height: 1.55;
  color: var(--el-text-color-primary, #1f2937);
  max-height: 60vh;
  overflow-y: auto;
}
.hfl-danger-confirm__message {
  margin: 0;
  white-space: pre-wrap;
}
.hfl-danger-confirm__warning,
.hfl-danger-confirm__items,
.hfl-danger-confirm__irreversible,
.hfl-danger-confirm__keyword,
.hfl-danger-confirm__pending,
.hfl-danger-confirm__error {
  margin-top: 14px;
}
.hfl-danger-confirm__message + .hfl-danger-confirm__warning,
.hfl-danger-confirm__message + .hfl-danger-confirm__items,
.hfl-danger-confirm__message + .hfl-danger-confirm__irreversible,
.hfl-danger-confirm__message + .hfl-danger-confirm__keyword {
  margin-top: 16px;
}
.hfl-danger-confirm__warning + .hfl-danger-confirm__items,
.hfl-danger-confirm__items + .hfl-danger-confirm__irreversible,
.hfl-danger-confirm__irreversible + .hfl-danger-confirm__keyword {
  margin-top: 14px;
}
.hfl-danger-confirm__pending,
.hfl-danger-confirm__error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.5;
}
.hfl-danger-confirm__pending {
  background: var(--color-info-light);
  border: 1px solid var(--color-info-border);
  color: var(--color-info);
}
.hfl-danger-confirm__error {
  background: var(--color-error-light);
  border: 1px solid var(--color-error-border);
  color: var(--color-error);
}
.hfl-danger-confirm__pending-icon {
  flex: 0 0 18px;
  animation: hfl-danger-confirm-spin 900ms linear infinite;
}
.hfl-danger-confirm__error :deep(svg) {
  flex: 0 0 16px;
  margin-top: 2px;
}
.hfl-danger-confirm__warning {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 8px 12px;
  border-radius: 8px;
  background: var(--color-warning-light);
  border: 1px solid var(--color-warning-border);
  color: var(--color-warning);
  font-size: 13px;
  line-height: 1.5;
}
.hfl-danger-confirm__warning :deep(svg) {
  flex: 0 0 16px;
  margin-top: 2px;
}
.hfl-danger-confirm__items {
  border-radius: 8px;
  background: var(--el-fill-color-blank, #fafafa);
  overflow: hidden;
}
.hfl-danger-confirm__items-heading {
  padding: 10px 12px 8px;
  background: var(--el-fill-color-blank, #fafafa);
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary, #4b5563);
}
.hfl-danger-confirm__item-table-scroll {
  max-height: 208px;
  overflow: auto;
}
.hfl-danger-confirm__item-table {
  table-layout: fixed;
  min-width: 440px;
}
.hfl-danger-confirm__item-table th {
  position: sticky;
  top: 0;
  z-index: 1;
}
.hfl-danger-confirm__item-table th,
.hfl-danger-confirm__item-table td {
  padding-right: 12px;
  padding-left: 12px;
}
.hfl-danger-confirm__item-table td {
  min-width: 0;
  overflow: hidden;
  vertical-align: top;
}
.hfl-danger-confirm__item-col-name {
  width: 36%;
}
.hfl-danger-confirm__item-col-status {
  width: 110px;
}
.hfl-danger-confirm__item-col-details {
  width: auto;
}
.hfl-danger-confirm__item-name {
  display: block;
  font-weight: 500;
  color: var(--el-text-color-primary, #111827);
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hfl-danger-confirm__item-desc {
  display: block;
  min-width: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary, #6b7280);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.hfl-danger-confirm__item-details {
  display: grid;
  gap: 5px;
  min-width: 0;
}
.hfl-danger-confirm__item-warning {
  display: grid;
  grid-template-columns: 12px minmax(0, 1fr);
  min-width: 0;
  align-items: flex-start;
  gap: 4px;
  color: var(--color-warning-text, var(--color-warning));
  font-size: 11px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.hfl-danger-confirm__item-warning :deep(svg) {
  flex: 0 0 12px;
  margin-top: 2px;
}
.hfl-danger-confirm__item-warning > span {
  min-width: 0;
  overflow-wrap: anywhere;
}
.hfl-danger-confirm__status {
  display: inline-block;
  box-sizing: border-box;
  max-width: 100%;
  overflow: hidden;
  font-size: 11px;
  line-height: 1;
  padding: 3px 8px;
  border-radius: 999px;
  background: rgba(0, 0, 0, 0.05);
  text-overflow: ellipsis;
  vertical-align: middle;
  white-space: nowrap;
}
.hfl-danger-confirm__status--success { background: var(--color-success-light); color: var(--color-success); }
.hfl-danger-confirm__status--warning { background: var(--color-warning-light); color: var(--color-warning); }
.hfl-danger-confirm__status--danger  { background: var(--color-error-light); color: var(--color-error); }
.hfl-danger-confirm__status--info    { background: var(--color-info-light); color: var(--color-info); }
.hfl-danger-confirm__status--neutral { background: #f3f4f6; color: #4b5563; }

@media (max-width: 560px) {
  .hfl-danger-confirm__item-table-scroll {
    overflow-x: hidden;
  }

  .hfl-danger-confirm__item-table {
    min-width: 0;
    table-layout: auto;
  }

  .hfl-danger-confirm__item-table colgroup {
    display: none;
  }

  .hfl-danger-confirm__item-table thead {
    position: absolute;
    width: 1px;
    height: 1px;
    padding: 0;
    margin: -1px;
    overflow: hidden;
    clip: rect(0, 0, 0, 0);
    white-space: nowrap;
    border: 0;
  }

  .hfl-danger-confirm__item-table tbody {
    display: block;
  }

  .hfl-danger-confirm__item-table tr {
    display: grid;
    grid-template-columns: minmax(0, 1fr) minmax(88px, 42%);
    gap: 8px 12px;
    padding: 12px;
    border-bottom: 1px solid var(--color-border);
    background: var(--el-fill-color-blank, #fff);
  }

  .hfl-danger-confirm__item-table tr:last-child {
    border-bottom: 0;
  }

  .hfl-danger-confirm__item-table td,
  .hfl-danger-confirm__item-table tr:hover td {
    display: block;
    padding: 0;
    border: 0;
    background: transparent;
  }

  .hfl-danger-confirm__item-status-cell {
    max-width: 100%;
    justify-self: end;
  }

  .hfl-danger-confirm__item-name-cell--full {
    grid-column: 1 / -1;
  }

  .hfl-danger-confirm__item-name,
  .hfl-danger-confirm__status {
    overflow: visible;
    text-overflow: clip;
    white-space: normal;
    overflow-wrap: anywhere;
  }

  .hfl-danger-confirm__status {
    line-height: 1.3;
    text-align: right;
  }

  .hfl-danger-confirm__item-details-cell {
    grid-column: 1 / -1;
  }

  .hfl-danger-confirm__item-warning {
    font-size: 12px;
    line-height: 1.5;
  }

  .hfl-danger-confirm__item-details-cell--empty {
    display: none !important;
  }
}

.hfl-danger-confirm__irreversible {
  margin: 4px 0 0;
  padding: 8px 12px;
  border-radius: 6px;
  font-size: 12px;
  line-height: 1.5;
}
.hfl-danger-confirm__irreversible--danger {
  background: color-mix(in srgb, var(--color-error) 6%, transparent);
  color: var(--color-error);
}
.hfl-danger-confirm__irreversible--neutral {
  background: var(--el-fill-color-light, #f3f4f6);
  color: var(--el-text-color-secondary, #6b7280);
}
.hfl-danger-confirm__keyword {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Reusable body primitives for business-specific confirmation content. */
.hfl-danger-confirm__section {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.hfl-danger-confirm__section-title {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 0;
  color: var(--el-text-color-primary, #111827);
  font-size: 13px;
  font-weight: 700;
  line-height: 1.4;
}

.hfl-danger-confirm__section-title::before {
  display: inline-block;
  width: 3px;
  height: 14px;
  border-radius: 999px;
  background: var(--color-primary);
  content: '';
}

.hfl-danger-confirm__impact,
.hfl-danger-confirm__risk {
  padding: 10px 12px;
  border-radius: 8px;
  font-size: 13px;
  line-height: 1.5;
}

.hfl-danger-confirm__impact {
  border: 1px solid var(--color-error-border);
  background: var(--color-error-light);
  color: var(--color-error);
}

.hfl-danger-confirm__impact--warning {
  border-color: var(--color-warning-border);
  background: var(--color-warning-light);
  color: var(--color-warning);
}

.hfl-danger-confirm__impact-title,
.hfl-danger-confirm__risk-title {
  margin: 0 0 8px;
  font-size: 13px;
  font-weight: 700;
  line-height: 1.4;
}

.hfl-danger-confirm__impact-list {
  display: grid;
  gap: 8px;
  margin: 0;
  padding: 0;
  list-style: none;
}

.hfl-danger-confirm__impact-item {
  display: grid;
  grid-template-columns: 22px minmax(0, 1fr);
  gap: 8px;
  align-items: flex-start;
}

.hfl-danger-confirm__impact-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  border: 1px solid currentColor;
  border-radius: 999px;
  background: var(--el-fill-color-blank, #fff);
  font-size: 11px;
  font-weight: 700;
  line-height: 1;
}

.hfl-danger-confirm__impact-text {
  min-width: 0;
  overflow-wrap: anywhere;
}

.hfl-danger-confirm__impact-note {
  margin: 10px 0 0;
  padding-top: 10px;
  border-top: 1px solid color-mix(in srgb, currentColor 18%, transparent);
  font-size: 12px;
  line-height: 1.5;
}

.hfl-danger-confirm__risk {
  border: 1px solid var(--color-warning-border);
  background: var(--color-warning-light);
  color: var(--el-text-color-regular, #374151);
}

.hfl-danger-confirm__risk ul {
  margin: 0;
  padding-left: 1.1rem;
}

.hfl-danger-confirm__risk li + li {
  margin-top: 4px;
}

.hfl-danger-confirm__force {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  margin: 0;
  cursor: pointer;
  font-size: 13px;
  line-height: 1.5;
  color: var(--el-text-color-regular, #374151);
}

.hfl-danger-confirm__force-label {
  display: block;
  font-weight: 600;
}

.hfl-danger-confirm__force-hint {
  display: block;
  margin-top: 4px;
  color: var(--el-text-color-secondary, #6b7280);
}

/* ===== footer ===== */
.hfl-danger-confirm__footer {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
  padding: var(--dcf-pad-y-footer) var(--dcf-pad-x);
  background: var(--el-fill-color-blank, #fafafa);
}

@keyframes hfl-danger-confirm-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>

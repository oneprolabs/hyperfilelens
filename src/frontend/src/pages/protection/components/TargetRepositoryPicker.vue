<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, Cloud, HardDrive, Layers, RefreshCw, Search, Server } from 'lucide-vue-next'
import TargetRepositoryDetailCard from './TargetRepositoryDetailCard.vue'

export type NasTargetMode = 'single_repo' | 'per_directory_repo'

export type TargetRepositoryStatus = 'online' | 'warning' | 'offline'
export type TargetRepositoryHealth = 'online' | 'offline' | 'unverified' | string

export type TargetRepositoryItem = {
  id: string
  name: string
  location: string
  repoType: string
  status?: TargetRepositoryStatus
  health?: TargetRepositoryHealth
  nasProtocol?: 'nfs' | 'smb' | string | null
  bindNodeType?: 'proxy' | string | null
  bindNodeId?: number | string | null
  bindNodeName?: string | null
  bindNodeIp?: string | null
  s3Endpoint?: string | null
  s3Region?: string | null
  s3Bucket?: string | null
  nasServerAddress?: string | null
  nasSharePath?: string | null
  proxyNodeDir?: string | null
  usedBytes?: number
  capacityBytes?: number
  storageTotalBytes?: number
  storageUsedBytes?: number
  storageAvailableBytes?: number
  disabled?: boolean
  disabledReason?: string | null
  disabledReasonItems?: string[]
}

const props = withDefaults(defineProps<{
  modelValue: string
  repoType: string
  nasMode?: NasTargetMode
  targets: TargetRepositoryItem[]
  selectedTarget?: TargetRepositoryItem | null
  targetPlaceholder: string
  repoTypePlaceholder: string
  noDataText: string
  refreshable?: boolean
  refreshing?: boolean
  refreshTitle?: string
}>(), {
  nasMode: 'per_directory_repo',
  selectedTarget: null,
  refreshable: false,
  refreshing: false,
  refreshTitle: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:repoType': [value: string]
  'update:nasMode': [value: NasTargetMode]
  refresh: []
}>()

const { t } = useI18n()
const radioGroupName = useId()
const localSearch = ref('')
const inspectedId = ref('')
const resultsRef = ref<HTMLDivElement | null>(null)
const typeOptions = [
  { key: 'repoTypeAll', value: '', icon: Layers },
  { key: 'repoTypeS3', value: 'S3', icon: Cloud },
  { key: 'repoTypeNas', value: 'NAS', icon: Server },
  { key: 'repoTypeProxyFs', value: 'PROXY_FS', icon: HardDrive },
]
const availableSelection = computed(() =>
  props.targets.find((target) => target.id === props.modelValue),
)
const selected = computed(() =>
  availableSelection.value ??
    (props.selectedTarget?.id === props.modelValue ? props.selectedTarget : null),
)
const selectionUnavailable = computed(() => Boolean(props.modelValue && (
  !availableSelection.value || availableSelection.value.disabled ||
  !['online', 'unverified'].includes(health(availableSelection.value))
)))
const inspected = computed(() =>
  props.targets.find((target) => target.id === inspectedId.value) ?? selected.value,
)
const inspectingSelection = computed(() => inspected.value?.id === props.modelValue)
const filteredTargets = computed(() => {
  const query = localSearch.value.trim().toLowerCase()
  return props.targets.filter((target) =>
    (!props.repoType || target.repoType === props.repoType) &&
    (!query || [target.name, target.location, target.repoType, typeLabel(target.repoType)]
      .some((value) => value.toLowerCase().includes(query))),
  )
})
const selectionHidden = computed(() => !selectionUnavailable.value && selected.value &&
  !filteredTargets.value.some((target) => target.id === props.modelValue),
)

watch(() => props.modelValue, () => { inspectedId.value = '' })
watch(selected, (target) => {
  if (target?.repoType === 'NAS' && !selectionUnavailable.value) {
    emit('update:nasMode', target.bindNodeType === 'proxy' && target.bindNodeId
      ? 'single_repo' : 'per_directory_repo')
  }
}, { immediate: true })

function typeLabel(type: string) {
  const option = typeOptions.find((option) => option.value === type)
  return option ? t(`protection.backupsPage.${option.key}`) : type
}
function typeCount(type: string) {
  return props.targets.filter((target) => !type || target.repoType === type).length
}
function health(target: TargetRepositoryItem) {
  return target.health || target.status || 'unverified'
}
function healthLabel(target: TargetRepositoryItem) {
  const value = health(target)
  return t(`protection.backupsPage.${value === 'online' ? 'targetHealthOnline'
    : value === 'offline' ? 'targetHealthOffline'
      : value === 'unverified' ? 'targetHealthUnverified' : 'targetPickerUnknown'}`)
}
function choose(target: TargetRepositoryItem) {
  if (target.disabled) return
  inspectedId.value = ''
  if (target.id !== props.modelValue) emit('update:modelValue', target.id)
}
function restoreSelectedDetails(target: TargetRepositoryItem) {
  if (target.id === props.modelValue && !target.disabled) inspectedId.value = ''
}
async function locateSelection() {
  const targetId = props.modelValue
  localSearch.value = ''
  emit('update:repoType', '')
  inspectedId.value = ''
  await nextTick()

  const results = resultsRef.value
  if (!results || !targetId || targetId !== props.modelValue || selectionUnavailable.value) return
  const radio = Array.from(results.querySelectorAll<HTMLInputElement>('input[type="radio"]'))
    .find((input) => input.value === targetId)
  const row = radio?.closest<HTMLElement>('.target-repository-picker__row')
  if (!radio || !row || radio.disabled) return

  // Scroll only this list, not the surrounding dialog or wizard.
  const viewportTop = results.getBoundingClientRect().top + results.clientTop
  const viewportBottom = viewportTop + results.clientHeight
  const bounds = row.getBoundingClientRect()
  if (bounds.top < viewportTop || bounds.height > results.clientHeight) {
    results.scrollTop += bounds.top - viewportTop
  } else if (bounds.bottom > viewportBottom) {
    results.scrollTop += bounds.bottom - viewportBottom
  }
  radio.focus({ preventScroll: true })
}
</script>

<template>
  <div class="target-repository-picker">
    <div class="target-repository-picker__toolbar">
      <ElInput
        v-model="localSearch"
        clearable
        :placeholder="targetPlaceholder"
        :aria-label="t('protection.backupsPage.targetRepositorySearchLabel')"
      >
        <template #prefix>
          <Search :size="16" />
        </template>
      </ElInput>
      <ElButton
        v-if="refreshable"
        class="hfl-refresh-button"
        :title="refreshTitle || t('common.refresh')"
        :aria-label="refreshTitle || t('common.refresh')"
        :disabled="refreshing"
        @click="emit('refresh')"
      >
        <RefreshCw
          :size="16"
          :class="{ 'is-spinning': refreshing }"
        />
      </ElButton>
    </div>
    <div
      class="target-repository-picker__filters"
      role="group"
      :aria-label="repoTypePlaceholder"
    >
      <button
        v-for="option in typeOptions"
        :key="option.value"
        type="button"
        class="target-repository-picker__filter"
        :aria-pressed="repoType === option.value"
        @click="emit('update:repoType', option.value)"
      >
        <component
          :is="option.icon"
          :size="15"
        />
        {{ t(`protection.backupsPage.${option.key}`) }}
        <span class="target-repository-picker__count">{{ typeCount(option.value) }}</span>
      </button>
    </div>
    <div
      v-if="selectionUnavailable"
      class="target-repository-picker__notice target-repository-picker__notice--unavailable"
      role="status"
    >
      {{ t('protection.backupsPage.targetPickerSelectionUnavailable') }}
    </div>
    <div
      v-else-if="selectionHidden"
      class="target-repository-picker__notice"
      role="status"
    >
      {{ t('protection.backupsPage.targetPickerSelectionHidden') }}
      <ElButton
        link
        type="primary"
        @click="locateSelection"
      >
        {{ t('protection.backupsPage.targetPickerLocate') }}
      </ElButton>
    </div>
    <div class="target-repository-picker__panels">
      <div
        ref="resultsRef"
        class="target-repository-picker__results scrollbar"
        :aria-busy="refreshing"
      >
        <div
          class="target-repository-picker__results-heading"
          aria-live="polite"
        >
          {{ t('protection.backupsPage.targetPickerResults', { n: filteredTargets.length }) }}
        </div>
        <div
          role="group"
          :aria-label="t('protection.backupsPage.targetRepositoryListLabel')"
        >
          <div
            v-for="target in filteredTargets"
            :key="target.id"
            class="target-repository-picker__row"
            :class="{ 'is-selected': target.id === modelValue, 'is-disabled': target.disabled }"
          >
            <label
              class="target-repository-picker__choice"
              @click="restoreSelectedDetails(target)"
            >
              <input
                type="radio"
                :name="radioGroupName"
                :checked="target.id === modelValue"
                :disabled="target.disabled"
                :value="target.id"
                @change="choose(target)"
              >
              <span class="target-repository-picker__row-content">
                <span class="target-repository-picker__row-heading">
                  <span class="target-repository-picker__name">{{ target.name }}</span>
                  <span
                    class="target-repository-picker__health"
                    :class="`is-${health(target)}`"
                  >
                    <span aria-hidden="true" />{{ healthLabel(target) }}
                  </span>
                </span>
                <span class="target-repository-picker__meta">
                  <span class="target-repository-picker__type">
                    {{ typeLabel(target.repoType) }}<template v-if="target.nasProtocol"> · {{ target.nasProtocol.toUpperCase() }}</template>
                  </span>
                  <span
                    class="target-repository-picker__location"
                    :title="target.location"
                  >{{ target.location }}</span>
                </span>
              </span>
            </label>
            <div
              v-if="target.disabled"
              class="target-repository-picker__disabled-reason"
            >
              <span>{{ target.disabledReason }}</span>
              <ElButton
                link
                type="primary"
                @click="inspectedId = target.id"
              >
                {{ t('protection.backupsPage.targetPickerViewReason') }}
              </ElButton>
            </div>
          </div>
        </div>
        <div
          v-if="!filteredTargets.length"
          class="target-repository-picker__empty"
          role="status"
        >
          <Search
            :size="28"
            aria-hidden="true"
          />
          {{ noDataText }}
        </div>
      </div>
      <aside
        class="target-repository-picker__detail scrollbar"
        :aria-label="t('protection.backupsPage.targetPickerDetails')"
      >
        <div class="target-repository-picker__detail-heading">
          <Check
            v-if="inspected && inspectingSelection"
            :size="16"
          />
          {{ t(`protection.backupsPage.${inspected && inspectingSelection ? 'targetRepositorySelectedLabel' : 'targetPickerDetails'}`) }}
        </div>
        <template v-if="inspected">
          <TargetRepositoryDetailCard :target="inspected" />
          <slot
            v-if="inspectingSelection && !selectionUnavailable"
            name="selection-options"
          />
          <ElButton
            v-else-if="selected"
            link
            type="primary"
            @click="inspectedId = ''"
          >
            {{ t('protection.backupsPage.targetPickerReturnToSelection') }}
          </ElButton>
        </template>
        <div
          v-else
          class="target-repository-picker__empty"
        >
          <HardDrive
            :size="32"
            aria-hidden="true"
          />
          <strong>{{ t('protection.backupsPage.targetPickerChoose') }}</strong>
          <span>{{ t('protection.backupsPage.targetPickerChooseHint') }}</span>
        </div>
      </aside>
    </div>
    <div
      class="target-repository-picker__summary"
      role="status"
    >
      <Check
        v-if="selected"
        :size="16"
        aria-hidden="true"
      />
      <span>{{ selected
        ? t('protection.backupsPage.targetPickerSelected', { name: selected.name })
        : t('protection.backupsPage.targetPickerChoose') }}</span>
    </div>
  </div>
</template>

<style scoped>
.target-repository-picker {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 12px;
  color: var(--el-text-color-primary);
}

.target-repository-picker__toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
}

.target-repository-picker__filters {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.target-repository-picker__filter {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 36px;
  padding: 6px 12px;
  border: 1px solid var(--el-border-color);
  border-radius: 7px;
  background: var(--el-bg-color-overlay);
  color: var(--el-text-color-regular);
  font-size: 13px;
  cursor: pointer;
}

.target-repository-picker__filter[aria-pressed='true'] {
  border-color: var(--color-primary);
  background: color-mix(in srgb, var(--color-primary) 8%, var(--el-bg-color-overlay));
  color: var(--color-primary);
}

.target-repository-picker__filter:focus-visible,
.target-repository-picker__choice:has(input:focus-visible) {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.target-repository-picker__count {
  padding: 1px 6px;
  border-radius: 4px;
  background: var(--el-fill-color);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.target-repository-picker__panels {
  display: grid;
  grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
  align-items: stretch;
  border: 1px solid var(--el-border-color);
  border-radius: 10px;
  overflow: hidden;
}

.target-repository-picker__results,
.target-repository-picker__detail {
  min-width: 0;
  max-height: min(480px, 48dvh);
  overflow-y: auto;
}

.target-repository-picker__results {
  padding: 12px 12px 8px;
  background: var(--el-bg-color-overlay);
}

.target-repository-picker__results-heading {
  padding: 0 4px 10px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.target-repository-picker__row {
  border-bottom: 1px solid var(--el-border-color-light);
}

.target-repository-picker__row:last-child {
  border-bottom: 0;
}

.target-repository-picker__row:hover:not(.is-disabled) {
  background: var(--el-fill-color-light);
}

.target-repository-picker__row.is-selected,
.target-repository-picker__row.is-selected:hover {
  background: color-mix(in srgb, var(--color-primary) 5%, var(--el-bg-color-overlay));
  box-shadow: inset 3px 0 0 var(--color-primary);
}

.target-repository-picker__choice {
  display: flex;
  min-height: 56px;
  box-sizing: border-box;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  cursor: pointer;
}

.target-repository-picker__choice input {
  width: 16px;
  height: 16px;
  flex: 0 0 16px;
  margin: 0;
  accent-color: var(--color-primary);
}

.target-repository-picker__row-content {
  display: grid;
  min-width: 0;
  flex: 1;
  gap: 2px;
}

.target-repository-picker__row-heading {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
}

.target-repository-picker__name {
  min-width: 0;
  overflow-wrap: anywhere;
  font-size: 14px;
  font-weight: 600;
  line-height: 20px;
}

.target-repository-picker__health {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 5px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.target-repository-picker__health > span {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: currentcolor;
}

.target-repository-picker__health.is-online {
  color: var(--el-color-success);
}

.target-repository-picker__health.is-unverified {
  color: var(--el-color-warning);
}

.target-repository-picker__health.is-offline {
  color: var(--el-color-danger);
}

.target-repository-picker__meta {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 8px;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.target-repository-picker__type {
  flex: 0 0 auto;
}

.target-repository-picker__location {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  padding-left: 8px;
  border-left: 1px solid var(--el-border-color);
  text-overflow: ellipsis;
  white-space: nowrap;
  font-family: var(--font-mono, monospace);
}

.target-repository-picker__row.is-disabled .target-repository-picker__choice {
  color: var(--el-text-color-secondary);
  cursor: not-allowed;
}

.target-repository-picker__disabled-reason {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 6px;
  padding: 0 12px 8px 36px;
  color: var(--el-color-warning);
  font-size: 12px;
}

.target-repository-picker__detail {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 20px;
  border-left: 1px solid var(--el-border-color);
  background: var(--el-fill-color-light);
}

.target-repository-picker__detail-heading {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--el-text-color-secondary);
}

.target-repository-picker__empty {
  display: flex;
  min-height: 180px;
  flex: 1;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 12px;
  padding: 20px;
  color: var(--el-text-color-secondary);
  text-align: center;
  font-size: 13px;
  line-height: 1.6;
}

.target-repository-picker__notice,
.target-repository-picker__summary {
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 8px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}

.target-repository-picker__summary span {
  min-width: 0;
  overflow-wrap: anywhere;
}

.target-repository-picker__notice--unavailable {
  color: var(--el-color-warning);
}

.target-repository-picker__summary svg {
  flex-shrink: 0;
  color: var(--color-primary);
}

@media (max-width: 760px) {
  .target-repository-picker__panels {
    grid-template-columns: minmax(0, 1fr);
  }

  .target-repository-picker__results {
    max-height: 32dvh;
  }

  .target-repository-picker__detail {
    max-height: none;
    border-top: 1px solid var(--el-border-color);
    border-left: 0;
  }

  .target-repository-picker__row-heading {
    flex-wrap: wrap;
    gap: 4px;
  }

  .target-repository-picker__meta {
    align-items: stretch;
    flex-direction: column;
    gap: 2px;
  }

  .target-repository-picker__location {
    padding-left: 0;
    border-left: 0;
  }
}
</style>

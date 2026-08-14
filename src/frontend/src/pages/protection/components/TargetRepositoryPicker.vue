<script setup lang="ts">
import { computed, markRaw, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Cloud, HardDrive, Layers, RefreshCw, Server } from 'lucide-vue-next'
import HflPopover from '../../../components/HflPopover.vue'
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
  repoTypeOptions: string[]
  targetPlaceholder: string
  repoTypePlaceholder: string
  noDataText: string
  teleported?: boolean
  showNasMode?: boolean
  compact?: boolean
  refreshable?: boolean
  refreshing?: boolean
  refreshTitle?: string
}>(), {
  nasMode: 'per_directory_repo',
  selectedTarget: null,
  teleported: true,
  showNasMode: true,
  compact: false,
  refreshable: false,
  refreshing: false,
  refreshTitle: '',
})

const emit = defineEmits<{
  'update:modelValue': [value: string]
  'update:repoType': [value: string]
  'update:nasMode': [value: NasTargetMode]
  search: [query: string]
  visibleChange: [visible: boolean]
  refresh: []
}>()

const { t } = useI18n()
const ALL_REPO_TYPE_VALUE = '__all__'
const localSearch = ref('')
type TargetSelectExpose = {
  focus?: () => void
  toggleMenu?: () => void
}
const targetSelectRef = ref<TargetSelectExpose | null>(null)
const targetSelectOpen = ref(false)
type HflPopoverInstance = InstanceType<typeof HflPopover>
const optionPopoverRefs = ref<HflPopoverInstance[]>([])
let openTargetSelectTimer: ReturnType<typeof setTimeout> | null = null

const resolvedSelectedTarget = computed<TargetRepositoryItem | null>(() =>
  props.selectedTarget ?? props.targets.find((target) => target.id === props.modelValue) ?? null,
)


function autoNasModeForTarget(target: TargetRepositoryItem | null | undefined): NasTargetMode {
  if (!target || target.repoType !== 'NAS') return 'per_directory_repo'
  if (target.bindNodeType === 'proxy' && target.bindNodeId) return 'single_repo'
  return 'per_directory_repo'
}

watch(
  () => resolvedSelectedTarget.value,
  (target) => {
    if (target && target.repoType === 'NAS') {
      emit('update:nasMode', autoNasModeForTarget(target))
    }
  },
  { immediate: true },
)



const TARGET_TYPE_LABELS: Record<string, string> = {
  S3: 'protection.backupsPage.repoTypeS3',
  NAS: 'protection.backupsPage.repoTypeNas',
  PROXY_FS: 'protection.backupsPage.repoTypeProxyFs',
}

function labelForRepoType(type: string): string {
  if (type === 'S3') return 'Object Storage'
  if (type === 'NAS') return 'NAS'
  if (type === 'PROXY_FS') return 'Local Disk'
  const key = TARGET_TYPE_LABELS[type]
  if (key) return String(t(key))
  return type
}

const repoTypeFilterOptions = [
  { label: 'All', value: ALL_REPO_TYPE_VALUE, icon: markRaw(Layers) },
  { label: 'Object Storage', value: 'S3', icon: markRaw(Cloud) },
  { label: 'NAS', value: 'NAS', icon: markRaw(Server) },
  { label: 'Local Disk', value: 'PROXY_FS', icon: markRaw(HardDrive) },
]

const activeRepoTypeValue = computed(() => props.repoType || ALL_REPO_TYPE_VALUE)

const filteredTargets = computed(() => {
  const query = localSearch.value.trim().toLowerCase()
  if (!query) return props.targets
  return props.targets.filter((target) =>
    target.name.toLowerCase().includes(query) ||
    target.location.toLowerCase().includes(query) ||
    target.repoType.toLowerCase().includes(query),
  )
})

function updateRepoType(value: string) {
  emit('update:repoType', value === ALL_REPO_TYPE_VALUE ? '' : value)
}

function isRepoTypeFilterChecked(value: string): boolean {
  return activeRepoTypeValue.value === value
}

function repoTypeFilterCount(value: string): number {
  if (value === ALL_REPO_TYPE_VALUE) return props.targets.length
  return props.targets.filter((target) => target.repoType === value).length
}

function clearTargetSearch() {
  localSearch.value = ''
  emit('search', '')
}

function cancelScheduledTargetSelectOpen() {
  if (openTargetSelectTimer === null) return
  clearTimeout(openTargetSelectTimer)
  openTargetSelectTimer = null
}

function scheduleTargetSelectOpen(delay = 0) {
  cancelScheduledTargetSelectOpen()
  void nextTick(() => {
    openTargetSelectTimer = setTimeout(() => {
      const select = targetSelectRef.value
      select?.focus?.()
      if (!targetSelectOpen.value) {
        select?.toggleMenu?.()
      }
      openTargetSelectTimer = null
    }, delay)
  })
}

function selectRepoTypeFilter(value: string) {
  clearTargetSearch()
  if (!isRepoTypeFilterChecked(value)) {
    updateRepoType(value)
  }
  scheduleTargetSelectOpen()
}

function updateSearch(query: string) {
  localSearch.value = query
  emit('search', query)
}

function hideOptionPopovers() {
  for (const popover of optionPopoverRefs.value) {
    popover?.hide?.()
  }
}

function updateTargetValue(value: unknown) {
  hideOptionPopovers()
  emit('update:modelValue', String(value || ''))
}

function handleVisibleChange(visible: boolean) {
  targetSelectOpen.value = visible
  if (visible) localSearch.value = ''
  else hideOptionPopovers()
  emit('visibleChange', visible)
}

onMounted(() => {
  if (activeRepoTypeValue.value === ALL_REPO_TYPE_VALUE && !props.modelValue) {
    scheduleTargetSelectOpen(120)
  }
})

onBeforeUnmount(() => {
  cancelScheduledTargetSelectOpen()
  hideOptionPopovers()
})

function resolveHealth(target: TargetRepositoryItem): TargetRepositoryHealth {
  if (target.health) return target.health
  return target.status ?? 'unverified'
}

function healthLabel(health: TargetRepositoryHealth): string {
  if (health === 'online') return 'Online'
  if (health === 'offline') return 'Offline'
  if (health === 'unverified') return 'Unverified'
  return 'Unknown'
}

function healthTagType(health: TargetRepositoryHealth): 'success' | 'warning' | 'danger' | 'info' {
  if (health === 'online') return 'success'
  if (health === 'offline') return 'danger'
  if (health === 'unverified') return 'warning'
  return 'info'
}

function nasProtocolLabel(protocol?: string | null): string {
  if (!protocol) return ''
  if (protocol === 'nfs') return 'NFS'
  if (protocol === 'smb') return 'SMB'
  return protocol.toUpperCase()
}
</script>

<template>
  <div
    class="target-repository-picker"
    :class="{ 'target-repository-picker--compact': compact }"
  >
    <ElFormItem>
      <template #label>
        <span class="target-repository-picker__label-row">
          <span>{{ t('protection.backupsPage.targetRepositoryListLabel') }}</span>
          <ElButton
            v-if="refreshable"
            class="hfl-refresh-button target-repository-picker__refresh"
            :title="refreshTitle || t('common.refresh')"
            :aria-label="refreshTitle || t('common.refresh')"
            :disabled="refreshing"
            @click.stop="emit('refresh')"
          >
            <RefreshCw
              :size="15"
              stroke-width="2"
              :class="{ 'is-spinning': refreshing }"
            />
          </ElButton>
        </span>
      </template>
      <div class="target-repository-picker__selector">
        <div
          class="target-repository-picker__type-panel"
          role="listbox"
          :aria-label="repoTypePlaceholder"
        >
          <button
            v-for="option in repoTypeFilterOptions"
            :key="option.value"
            type="button"
            class="target-repository-picker__type-option"
            :class="{ 'is-selected': isRepoTypeFilterChecked(option.value) }"
            role="option"
            :aria-selected="isRepoTypeFilterChecked(option.value)"
            @click="selectRepoTypeFilter(option.value)"
          >
            <span class="target-repository-picker__type-option-content">
              <component
                :is="option.icon"
                :size="14"
                stroke-width="2"
                class="target-repository-picker__type-option-icon"
              />
              <span class="target-repository-picker__type-option-label">{{ option.label }}</span>
            </span>
            <span class="target-repository-picker__type-option-count">{{ repoTypeFilterCount(option.value) }}</span>
          </button>
        </div>

        <div class="target-repository-picker__select-column">
          <el-select
            ref="targetSelectRef"
            :model-value="modelValue"
            filterable
            clearable
            :teleported="teleported"
            :filter-method="updateSearch"
            :reserve-keyword="false"
            :placeholder="targetPlaceholder"
            :no-data-text="noDataText"
            popper-class="target-repository-picker-popper"
            @update:model-value="updateTargetValue"
            @visible-change="handleVisibleChange"
          >
            <el-option
              v-for="target in filteredTargets"
              :key="target.id"
              :label="target.name"
              :value="target.id"
              :disabled="target.disabled"
            >
              <HflPopover
                ref="optionPopoverRefs"
                trigger="hover"
                placement="right-start"
                :width="420"
                :show-after="300"
                :persistent="false"
                teleported
                :disabled="!target.repoType"
              >
                <template #reference>
                  <div
                    class="target-repository-picker-option"
                    :class="{ 'is-disabled': target.disabled }"
                    @pointerdown.capture="hideOptionPopovers"
                  >
                    <div class="target-repository-picker-option__head">
                      <span class="target-repository-picker-option__name">{{ target.name }}</span>
                      <span class="target-repository-picker-option__tags">
                        <el-tag
                          size="small"
                          effect="plain"
                          :class="`wizard-repo-type-tag wizard-repo-type-tag--${target.repoType.toLowerCase()}`"
                        >
                          {{ labelForRepoType(target.repoType) }}
                        </el-tag>
                        <el-tag
                          v-if="target.repoType === 'NAS' && target.nasProtocol"
                          size="small"
                          effect="plain"
                          :class="`wizard-protocol-tag wizard-protocol-tag--${target.nasProtocol}`"
                        >
                          {{ nasProtocolLabel(target.nasProtocol) }}
                        </el-tag>
                        <el-tag
                          size="small"
                          :type="healthTagType(resolveHealth(target))"
                          effect="plain"
                        >
                          {{ healthLabel(resolveHealth(target)) }}
                        </el-tag>
                      </span>
                    </div>
                    <span class="target-repository-picker-option__location">{{ target.location }}</span>
                  </div>
                </template>
                <TargetRepositoryDetailCard :target="target" />
              </HflPopover>
            </el-option>
          </el-select>
        </div>
      </div>
    </ElFormItem>
  </div>
</template>

<style scoped>
.target-repository-picker {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.target-repository-picker-option.is-disabled {
  cursor: not-allowed;
  filter: grayscale(1);
  opacity: 0.52;
}

.target-repository-picker--compact {
  gap: 10px;
}

.target-repository-picker :deep(.el-form-item) {
  margin-bottom: 0;
}

.target-repository-picker :deep(.el-form-item__label) {
  padding-bottom: 7px;
  color: rgb(71 85 105);
  font-size: 13px;
  font-weight: 650;
  line-height: 18px;
}

.target-repository-picker__label-row {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-width: 0;
}

.target-repository-picker__refresh {
  width: 28px !important;
  height: 28px !important;
  min-width: 28px !important;
  min-height: 28px !important;
  flex: 0 0 28px;
  box-sizing: border-box;
  line-height: 1;
  --el-button-size: 28px;
  transform: translateZ(0);
  -webkit-font-smoothing: antialiased;
  backface-visibility: hidden;
}

.target-repository-picker__refresh :deep(svg) {
  display: block;
  shape-rendering: geometricPrecision;
  stroke-linecap: round;
  stroke-linejoin: round;
}

.target-repository-picker__refresh :deep(.el-icon) {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.target-repository-picker :deep(.el-select) {
  width: 100%;
}

.target-repository-picker__selector {
  display: grid;
  grid-template-columns: 178px minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
  width: 100%;
}

.target-repository-picker__type-panel {
  display: flex;
  min-width: 0;
  flex-direction: column;
  gap: 3px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: rgb(248 250 252);
  padding: 6px;
}

.target-repository-picker__type-option {
  position: relative;
  display: flex;
  width: 100%;
  min-height: 34px;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 0 9px 0 10px;
  border: 1px solid transparent;
  border-radius: 6px;
  background: transparent;
  color: rgb(71 85 105);
  cursor: pointer;
  font: inherit;
  text-align: left;
  transition:
    background 0.16s ease,
    border-color 0.16s ease,
    box-shadow 0.16s ease,
    color 0.16s ease;
}

.target-repository-picker__type-option::before {
  position: absolute;
  top: 7px;
  bottom: 7px;
  left: 0;
  width: 3px;
  border-radius: 0 999px 999px 0;
  background: transparent;
  content: '';
}

.target-repository-picker__type-option:hover {
  background: #ffffff;
  border-color: rgba(203, 213, 225, 0.82);
}

.target-repository-picker__type-option:focus-visible {
  outline: none;
  border-color: color-mix(in srgb, var(--color-primary) 46%, rgb(203 213 225));
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary) 14%, transparent);
}

.target-repository-picker__type-option.is-selected {
  background: #ffffff;
  border-color: color-mix(in srgb, var(--color-primary) 20%, rgb(203 213 225));
  color: color-mix(in srgb, var(--color-primary) 82%, #000);
  box-shadow: 0 1px 2px rgb(15 23 42 / 0.06);
}

.target-repository-picker__type-option.is-selected::before {
  background: var(--color-primary);
}

.target-repository-picker__type-option-content {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  gap: 7px;
  font-size: 12px;
  font-weight: 650;
  line-height: 1;
}

.target-repository-picker__type-option-icon {
  flex: 0 0 auto;
  color: rgb(100 116 139);
}

.target-repository-picker__type-option.is-selected .target-repository-picker__type-option-icon {
  color: color-mix(in srgb, var(--color-primary) 76%, #000);
}

.target-repository-picker__type-option-label {
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-repository-picker__type-option-count {
  display: inline-flex;
  min-width: 22px;
  height: 18px;
  align-items: center;
  justify-content: center;
  padding: 0 6px;
  border-radius: 999px;
  background: rgb(226 232 240);
  color: rgb(71 85 105);
  font-size: 11px;
  font-weight: 700;
  line-height: 18px;
}

.target-repository-picker__type-option.is-selected .target-repository-picker__type-option-count {
  background: color-mix(in srgb, var(--color-primary) 12%, #ffffff);
  color: color-mix(in srgb, var(--color-primary) 86%, #000);
}

.target-repository-picker__select-column {
  min-width: 0;
}

.target-repository-picker-option__location {
  overflow-wrap: anywhere;
  font-size: 12px;
  line-height: 1.45;
  color: rgb(100 116 139);
}

.target-repository-picker-option {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 2px 0;
  cursor: default;
}

.target-repository-picker-option__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  min-width: 0;
}

.target-repository-picker-option__name {
  min-width: 0;
  overflow: hidden;
  font-size: 13px;
  font-weight: 600;
  color: var(--color-primary);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.target-repository-picker-option__tags {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  gap: 6px;
}

@media (max-width: 767.98px) {
  .target-repository-picker__selector {
    grid-template-columns: minmax(0, 1fr);
  }

  .target-repository-picker__type-panel {
    display: grid;
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .target-repository-picker-option__head {
    align-items: flex-start;
    flex-direction: column;
    gap: 6px;
  }
}
</style>

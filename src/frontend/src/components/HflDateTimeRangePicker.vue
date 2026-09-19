<script setup lang="ts">
import { computed, nextTick, ref, useId, watch } from 'vue'
import { ElDatePicker } from 'element-plus'

export type HflDateTimeRangePreset = {
  value: string
  label: string
  hours?: number
}

type RangeValue = [string, string] | ''

const props = withDefaults(
  defineProps<{
    label: string
    presets?: HflDateTimeRangePreset[]
    selectedPreset?: string
    start?: string
    end?: string
    clearText: string
    applyText: string
    constrainToTrigger?: boolean
    disabled?: boolean
    /** When set, custom ranges longer than this many hours are blocked. */
    maxSpanHours?: number
  }>(),
  {
    presets: () => [],
    selectedPreset: '',
    start: '',
    end: '',
    constrainToTrigger: false,
    disabled: false,
    maxSpanHours: undefined,
  },
)

const emit = defineEmits<{
  preset: [value: string, hours?: number]
  apply: [start: string, end: string]
  clear: []
  'invalid-span': []
}>()

const pickerValue = ref<RangeValue>('')
const suppressNextChange = ref(false)
const suppressNextClearChange = ref(false)
const selectingAnchor = ref<Date | null>(null)
const rangeInputId = useId()
const rangeInputIds: [string, string] = [
  `${rangeInputId}-start`,
  `${rangeInputId}-end`,
]
const rangeInputNames: [string, string] = [
  `${rangeInputId}-start`,
  `${rangeInputId}-end`,
]

const displayPlaceholder = computed(() => props.label || '')
const maxSpanMs = computed(() => (
  props.maxSpanHours && props.maxSpanHours > 0
    ? props.maxSpanHours * 60 * 60 * 1000
    : null
))
const popperClass = computed(() => [
  'hfl-date-time-range-picker__popper',
  props.constrainToTrigger ? 'hfl-date-time-range-picker__popper--constrained' : '',
].filter(Boolean).join(' '))

const shortcuts = computed(() => props.presets.flatMap((preset) => {
  const hours = preset.hours
  if (!hours) return []
  if (maxSpanMs.value != null && hours * 60 * 60 * 1000 > maxSpanMs.value) return []
  return [{
    text: preset.label,
    value: () => {
      const range = rangeForHours(hours)
      suppressNextChange.value = true
      selectingAnchor.value = null
      emit('preset', preset.value, hours)
      void nextTick(() => {
        pickerValue.value = displayRangeFromProps()
      })
      return range
    },
  }]
}))

function pad2(n: number) {
  return String(n).padStart(2, '0')
}

function formatLocalDateTime(date: Date) {
  return `${date.getFullYear()}-${pad2(date.getMonth() + 1)}-${pad2(date.getDate())}T${pad2(date.getHours())}:${pad2(date.getMinutes())}:${pad2(date.getSeconds())}`
}

function rangeFromProps(): RangeValue {
  if (!props.start || !props.end) return ''
  const start = normalizeDateTime(props.start)
  const end = normalizeDateTime(props.end)
  if (!start || !end) return ''
  return [start, end]
}

function normalizeDateTime(value: string) {
  const trimmed = value.trim()
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(trimmed)) return `${trimmed}:00`
  if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}/.test(trimmed)) return trimmed.slice(0, 19)
  return ''
}

function displayRangeFromProps(): RangeValue {
  const range = rangeFromProps()
  if (range) return range
  const preset = props.presets.find((item) => item.value === props.selectedPreset)
  const presetRange = preset ? rangeForPreset(preset) : null
  if (!presetRange) return ''
  return [formatLocalDateTime(presetRange[0]), formatLocalDateTime(presetRange[1])]
}

function rangeForPreset(preset: HflDateTimeRangePreset): [Date, Date] | null {
  if (!preset.hours) return null
  return rangeForHours(preset.hours)
}

function rangeForHours(hours: number): [Date, Date] {
  const end = new Date()
  const start = new Date(end.getTime() - hours * 60 * 60 * 1000)
  return [start, end]
}

function normalizeRange(value: unknown): RangeValue {
  if (!Array.isArray(value) || value.length !== 2) return ''
  const [start, end] = value
  if (!start || !end) return ''
  if (start instanceof Date && end instanceof Date) {
    return [formatLocalDateTime(start), formatLocalDateTime(end)]
  }
  return [String(start).slice(0, 19), String(end).slice(0, 19)]
}

function onUpdate(value: unknown) {
  pickerValue.value = normalizeRange(value)
}

function rangeExceedsMaxSpan(start: string, end: string) {
  if (maxSpanMs.value == null) return false
  const from = new Date(start).getTime()
  const to = new Date(end).getTime()
  if (!Number.isFinite(from) || !Number.isFinite(to) || to < from) return true
  return to - from > maxSpanMs.value
}

function disabledDate(date: Date) {
  if (maxSpanMs.value == null || !selectingAnchor.value) return false
  const anchor = selectingAnchor.value.getTime()
  const time = date.getTime()
  return time < anchor - maxSpanMs.value || time > anchor + maxSpanMs.value
}

function onCalendarChange(value: [Date, Date] | null) {
  if (!value?.[0] || value[1]) {
    selectingAnchor.value = null
    return
  }
  selectingAnchor.value = value[0]
}

function onChange(value: unknown) {
  let range = normalizeRange(value)
  if (range && isTodayEndOfDay(range[1])) {
    range = [range[0], formatLocalDateTime(new Date())]
  }
  pickerValue.value = range
  if (suppressNextChange.value) {
    suppressNextChange.value = false
    void nextTick(() => {
      pickerValue.value = displayRangeFromProps()
    })
    return
  }
  if (suppressNextClearChange.value) {
    suppressNextClearChange.value = false
    return
  }
  if (!range) {
    selectingAnchor.value = null
    emit('clear')
    return
  }
  if (rangeExceedsMaxSpan(range[0], range[1])) {
    selectingAnchor.value = null
    pickerValue.value = displayRangeFromProps()
    emit('invalid-span')
    return
  }
  selectingAnchor.value = null
  emit('apply', range[0], range[1])
}

function isTodayEndOfDay(value: string) {
  const date = new Date(value)
  const now = new Date()
  return Number.isFinite(date.getTime())
    && date.getFullYear() === now.getFullYear()
    && date.getMonth() === now.getMonth()
    && date.getDate() === now.getDate()
    && date.getHours() === 23
    && date.getMinutes() === 59
    && date.getSeconds() === 59
}

function onClear() {
  suppressNextClearChange.value = true
  selectingAnchor.value = null
  pickerValue.value = ''
  emit('clear')
}

watch(
  () => [props.start, props.end, props.selectedPreset, props.presets] as const,
  () => {
    pickerValue.value = displayRangeFromProps()
  },
  { deep: true, immediate: true },
)
</script>

<template>
  <div
    class="hfl-date-time-range-picker"
    :class="{ 'hfl-date-time-range-picker--constrained': constrainToTrigger }"
  >
    <ElDatePicker
      :id="rangeInputIds"
      :model-value="pickerValue"
      class="hfl-date-time-range-picker__trigger"
      type="datetimerange"
      unlink-panels
      format="YYYY-MM-DD HH:mm:ss"
      value-format="YYYY-MM-DDTHH:mm:ss"
      :default-time="[new Date(2000, 0, 1, 0, 0, 0), new Date(2000, 0, 1, 23, 59, 59)]"
      :name="rangeInputNames"
      :aria-label="label"
      :shortcuts="shortcuts"
      :disabled-date="disabledDate"
      :start-placeholder="displayPlaceholder"
      :end-placeholder="displayPlaceholder"
      :range-separator="'~'"
      :popper-class="popperClass"
      :disabled="disabled"
      clearable
      @update:model-value="onUpdate"
      @calendar-change="onCalendarChange"
      @change="onChange"
      @clear="onClear"
    />
  </div>
</template>

<style scoped>
.hfl-date-time-range-picker {
  width: max-content;
  max-width: 100%;
}

.hfl-date-time-range-picker--constrained,
.hfl-date-time-range-picker--constrained .hfl-date-time-range-picker__trigger {
  width: 100%;
}

.hfl-date-time-range-picker__trigger {
  width: 260px;
  max-width: 100%;
  height: 34px;
}

.hfl-date-time-range-picker__trigger :deep(.el-input__wrapper) {
  min-height: 34px;
}

.hfl-date-time-range-picker__trigger :deep(.el-range-input) {
  font-size: 13px;
}

.hfl-date-time-range-picker__trigger :deep(.el-range-separator) {
  padding: 0 4px;
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__shortcut) {
  min-height: 34px;
  padding: 0 12px;
  border-radius: var(--el-border-radius-base, 8px);
  line-height: 34px;
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__sidebar) {
  width: 130px;
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__sidebar + .el-picker-panel__body) {
  margin-left: 130px;
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer) {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  padding: 10px 14px;
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer .el-button) {
  height: 28px;
  min-height: 28px;
  margin-left: 0;
  padding: 0 14px;
  border-radius: var(--el-border-radius-base, 8px);
  font-size: 12px;
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer .el-button:not(.el-button--primary)) {
  border: 1px solid var(--color-border, var(--el-border-color, #dcdfe6));
  background: var(--el-fill-color-blank, #fff);
  color: var(--color-text-title, var(--el-text-color-primary, #303133));
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer .el-button:not(.el-button--primary):hover),
:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer .el-button:not(.el-button--primary):focus) {
  border-color: var(--el-color-primary, #409eff);
  background: var(--el-fill-color-light, #f5f7fa);
  color: var(--el-color-primary, #409eff);
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer .el-picker-panel__link-btn:last-child:not(.is-disabled)) {
  border-color: var(--el-color-primary, #409eff);
  background: var(--el-color-primary, #409eff);
  color: #fff;
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer .el-picker-panel__link-btn:last-child:not(.is-disabled):hover),
:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer .el-picker-panel__link-btn:last-child:not(.is-disabled):focus) {
  border-color: var(--color-primary-hover);
  background: var(--color-primary-hover);
  color: #fff !important;
}

:global(.hfl-date-time-range-picker__popper .el-picker-panel__footer .el-picker-panel__link-btn:last-child.is-disabled) {
  border-color: var(--el-disabled-border-color, #e4e7ed);
  background: var(--el-disabled-bg-color, #f5f7fa);
  color: var(--el-disabled-text-color, #a8abb2);
}

@media (max-width: 860px) {
  :global(.hfl-date-time-range-picker__popper) {
    max-width: calc(100vw - 24px);
    overflow-x: auto;
  }
}
</style>

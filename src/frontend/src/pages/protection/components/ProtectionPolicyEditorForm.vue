<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { BookOpen, Plus, TriangleAlert, Trash2 } from 'lucide-vue-next'
import {
  compileFilterIgnorePatterns,
  createEmptyFilterCustomRule,
  getScheduleTimezoneOptions,
  getSimpleIntervalUnitMeta,
  getSimpleIntervalUnitOptions,
  syncFilterFormIgnorePatterns,
  validateCronExpression,
  validateLongVsShortRetention,
  validateMidRetention,
  validateScheduleForm,
  type BackupPolicyForm,
  type FileFilterRuleForm,
  type MessageLocale,
} from '../../../lib/protectionPolicyFormModel'
import HflHelpTip from '../../../components/HflHelpTip.vue'

const props = withDefaults(defineProps<{
  showBackup?: boolean
  showFilter?: boolean
  variant?: 'fullscreen' | 'dialog'
  showCronHelp?: boolean
  cronInputId?: string
  nameError?: string
  scheduleError?: string
  retentionError?: string
}>(), {
  showBackup: true,
  showFilter: false,
  variant: 'fullscreen',
  showCronHelp: true,
  cronInputId: 'policy-cron-expr',
  nameError: '',
  scheduleError: '',
  retentionError: '',
})
const policyForm = defineModel<BackupPolicyForm>('policyForm', { required: true })
const filterForm = defineModel<FileFilterRuleForm>('filterForm', { required: true })
defineEmits<{ 'clear-name-error': [] }>()

const { t } = useI18n()
const router = useRouter()
const messageLocale = computed<MessageLocale>(() => 'en')
const simpleIntervalOptions = computed(() => getSimpleIntervalUnitOptions(messageLocale.value))
const scheduleTimezoneOptions = computed(() => getScheduleTimezoneOptions(policyForm.value.scheduleTimezone))
const quickScheduleTypeOptions = computed(() => [
  { value: 'interval' as const, label: t('protection.policiesPage.scheduleTypeInterval') },
  { value: 'daily' as const, label: t('protection.policiesPage.scheduleTypeDaily') },
  { value: 'weekly' as const, label: t('protection.policiesPage.scheduleTypeWeekly') },
  { value: 'monthly' as const, label: t('protection.policiesPage.scheduleTypeMonthly') },
])
const scheduleWeekdayOptions = computed(() => [
  { value: 1 as const, label: t('protection.policiesPage.weekdayMon') },
  { value: 2 as const, label: t('protection.policiesPage.weekdayTue') },
  { value: 3 as const, label: t('protection.policiesPage.weekdayWed') },
  { value: 4 as const, label: t('protection.policiesPage.weekdayThu') },
  { value: 5 as const, label: t('protection.policiesPage.weekdayFri') },
  { value: 6 as const, label: t('protection.policiesPage.weekdaySat') },
  { value: 7 as const, label: t('protection.policiesPage.weekdaySun') },
])
const scheduleMonthDayOptions = Array.from({ length: 31 }, (_, index) => index + 1)
const currentIntervalUnitMeta = computed(() =>
  getSimpleIntervalUnitMeta(policyForm.value.simpleIntervalUnit, messageLocale.value),
)
const cronValidation = computed(() => validateCronExpression(policyForm.value.cronExpr))
const cronErrorText = computed(() => {
  if (cronValidation.value.ok) return ''
  return cronValidation.value.reason === 'empty'
    ? t('protection.policiesPage.cronErrEmpty')
    : t('protection.policiesPage.cronErrFormat')
})
const scheduleErrorText = computed(() => validateScheduleForm(policyForm.value))
const midRetentionError = computed(() => validateMidRetention(policyForm.value, messageLocale.value))
const longVsShortRetentionError = computed(() => validateLongVsShortRetention(policyForm.value, messageLocale.value))
const hourlyRetentionError = computed(() =>
  policyForm.value.sectionRetentionEnabled && policyForm.value.retentionShortHourly
  && (typeof policyForm.value.retentionShortDaysMax !== 'number' || policyForm.value.retentionShortDaysMax < 1)
    ? t('protection.policiesPage.retentionHourlyRequired')
    : '',
)
const dailyRetentionError = computed(() =>
  policyForm.value.sectionRetentionEnabled && policyForm.value.retentionMidDaily
  && (typeof policyForm.value.retentionMidDaysMax !== 'number' || policyForm.value.retentionMidDaysMax < 1)
    ? t('protection.policiesPage.retentionDailyRequired')
    : '',
)
const monthlyRetentionError = computed(() =>
  policyForm.value.sectionRetentionEnabled && policyForm.value.retentionLongMonthly
  && (typeof policyForm.value.retentionLongMonths !== 'number' || policyForm.value.retentionLongMonths < 1)
    ? t('protection.policiesPage.retentionMonthlyRequired')
    : '',
)
const recentRetentionError = computed(() =>
  policyForm.value.sectionRetentionEnabled &&
  (typeof policyForm.value.retentionRecentPoints !== 'number' || policyForm.value.retentionRecentPoints < 1)
    ? 'Latest restore points must be at least 1.'
    : '',
)

function toggleHourlyRetention(enabled: boolean) {
  policyForm.value.retentionShortHourly = enabled
  policyForm.value.retentionHourlyEnabled = enabled
  if (!enabled) {
    policyForm.value.retentionShortDaysMax = undefined
    policyForm.value.retentionHourlyHours = undefined
  }
}

function toggleDailyRetention(enabled: boolean) {
  policyForm.value.retentionMidDaily = enabled
  policyForm.value.retentionDailyEnabled = enabled
  if (!enabled) {
    policyForm.value.retentionMidDaysMax = undefined
    policyForm.value.retentionDailyDays = undefined
  }
}

function toggleMonthlyRetention(enabled: boolean) {
  policyForm.value.retentionLongMonthly = enabled
  policyForm.value.retentionMonthlyEnabled = enabled
  if (!enabled) {
    policyForm.value.retentionLongMonths = undefined
    policyForm.value.retentionMonthlyMonths = undefined
  }
}

const formClass = computed(() =>
  props.variant === 'fullscreen'
    ? 'fullscreen-form-el-form fullscreen-form-el-form--strong-label fullscreen-form-step-stack'
    : 'policy-dialog-form policy-dialog-form--backup',
)
const filterFormClass = computed(() =>
  props.variant === 'fullscreen'
    ? 'fullscreen-form-el-form fullscreen-form-step-stack filter-rule-form'
    : 'policy-dialog-form filter-rule-form',
)
const sectionClass = computed(() =>
  props.variant === 'fullscreen'
    ? 'fullscreen-form-card fullscreen-form-section'
    : 'policy-dialog-card',
)
const nestedSectionClass = computed(() =>
  props.variant === 'fullscreen'
    ? 'fullscreen-form-card fullscreen-form-section'
    : 'policy-dialog-card policy-dialog-card--section',
)
const titleClass = computed(() =>
  props.variant === 'fullscreen'
    ? 'fullscreen-form-section__title'
    : 'policy-dialog-card__title',
)
const retentionInputWrapClass = computed(() =>
  props.variant === 'fullscreen' ? 'retention-recent-input-wrap' : 'retention-tier-input-wrap',
)

const errorHandlingRows = computed(() => [
  {
    key: 'errorIgnoreDirectory' as const,
    title: t('protection.policiesPage.errRow1Title'),
    desc: t('protection.policiesPage.errRow1Desc'),
  },
  {
    key: 'errorIgnoreFile' as const,
    title: t('protection.policiesPage.errRow2Title'),
    desc: t('protection.policiesPage.errRow2Desc'),
  },
  {
    key: 'errorIgnoreUnknownEntries' as const,
    title: t('protection.policiesPage.errRow3Title'),
    desc: t('protection.policiesPage.errRow3Desc'),
  },
])

const filterPresets = computed(() => [
  {
    key: 'presetTempFiles' as const,
    title: t('protection.policiesPage.filterPresetTempTitle'),
    desc: t('protection.policiesPage.filterPresetTempSub'),
  },
  {
    key: 'presetDevDeps' as const,
    title: t('protection.policiesPage.filterPresetDevTitle'),
    desc: t('protection.policiesPage.filterPresetDevSub'),
  },
  {
    key: 'presetSystemJunk' as const,
    title: t('protection.policiesPage.filterPresetSystemTitle'),
    desc: t('protection.policiesPage.filterPresetSystemSub'),
  },
])

const filterExcludeRules = computed(() =>
  filterForm.value.customRules
    .map((rule, index) => ({ rule, index }))
    .filter((item) => item.rule.type === 'exclude'),
)
const filterRulesTextMode = ref(false)
const filterRulesTextValue = ref('')
const compiledFilterRuleLines = computed(() => {
  return compileFilterIgnorePatterns(filterForm.value)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
})
const duplicateFilterRules = computed(() => {
  const seen = new Set<string>()
  const duplicates = new Set<string>()
  for (const line of rawFilterRuleLines()) {
    const normalized = normalizeRuleLine(line)
    if (!normalized) continue
    if (seen.has(normalized)) duplicates.add(normalized)
    else seen.add(normalized)
  }
  return [...duplicates]
})
const hasDuplicateFilterRules = computed(() => duplicateFilterRules.value.length > 0)
const filterTextRuleCount = computed(() => rawFilterRuleLines().length)

function onFilterPresetChange() {
  syncFilterFormIgnorePatterns(filterForm.value)
}

function onFilterCustomRuleChange() {
  syncFilterFormIgnorePatterns(filterForm.value)
}

function normalizeRuleLine(value: string) {
  return String(value || '').trim()
}

function rawFilterRuleLines() {
  return filterForm.value.customRules
    .filter((rule) => rule.type === 'exclude')
    .map((rule) => normalizeRuleLine(rule.pattern))
    .filter(Boolean)
}

function parseFilterRulesText(value: string) {
  return String(value || '')
    .split(/\r?\n/)
    .map(normalizeRuleLine)
    .filter(Boolean)
}

function replaceCustomRules(lines: string[]) {
  filterForm.value.customRules = lines.length
    ? lines.map((pattern) => ({ type: 'exclude', pattern }))
    : [createEmptyFilterCustomRule()]
  onFilterCustomRuleChange()
}

function customRulesToText() {
  return filterForm.value.customRules
    .filter((rule) => rule.type === 'exclude')
    .map((rule) => normalizeRuleLine(rule.pattern))
    .filter(Boolean)
    .join('\n')
}

function setFilterRulesTextMode(enabled: boolean) {
  if (enabled) {
    filterRulesTextValue.value = customRulesToText()
    filterRulesTextMode.value = true
    return
  }
  replaceCustomRules(parseFilterRulesText(filterRulesTextValue.value))
  filterRulesTextMode.value = false
}

function onFilterRulesTextInput(value: string) {
  filterRulesTextValue.value = value
  replaceCustomRules(parseFilterRulesText(value))
}

function setFilterCustomRulePattern(index: number, value: string) {
  const rule = filterForm.value.customRules[index]
  if (!rule) return
  rule.type = 'exclude'
  rule.pattern = value.trimStart()
  onFilterCustomRuleChange()
}

function onFilterCustomRulePaste(index: number, event: ClipboardEvent) {
  const text = event.clipboardData?.getData('text') || ''
  const lines = parseFilterRulesText(text)
  if (lines.length <= 1) return
  event.preventDefault()
  const rule = filterForm.value.customRules[index]
  if (!rule) return
  rule.type = 'exclude'
  rule.pattern = lines[0] || ''
  const insertRules = lines.slice(1).map((pattern) => ({ type: 'exclude' as const, pattern }))
  filterForm.value.customRules.splice(index + 1, 0, ...insertRules)
  onFilterCustomRuleChange()
}

function addFilterExcludeRule() {
  filterForm.value.customRules.push(createEmptyFilterCustomRule())
}

function removeFilterCustomRule(index: number) {
  if (filterForm.value.customRules.length <= 1) {
    filterForm.value.customRules[0] = createEmptyFilterCustomRule()
  } else {
    filterForm.value.customRules.splice(index, 1)
  }
  if (!filterForm.value.customRules.length) {
    filterForm.value.customRules.push(createEmptyFilterCustomRule())
  }
  onFilterCustomRuleChange()
}

function openFilterRuleGuide() {
  const href = router.resolve('/protection/file-filter-rules/help').href
  window.open(href, '_blank', 'noopener,noreferrer')
}

function onSimpleIntervalUnitChange() {
  const meta = currentIntervalUnitMeta.value
  const v = policyForm.value.simpleIntervalValue
  if (typeof v !== 'number' || v < meta.min || v > meta.max) {
    policyForm.value.simpleIntervalValue = meta.defaultValue
  }
}

function toggleScheduleMonthDay(day: number) {
  const days = new Set(policyForm.value.scheduleMonthDays)
  if (days.has(day)) days.delete(day)
  else days.add(day)
  policyForm.value.scheduleMonthDays = [...days].sort((a, b) => a - b)
}

</script>

<template>
  <ElForm
    v-show="showBackup"
    :model="policyForm"
    label-position="top"
    :class="formClass"
  >
    <section :class="sectionClass">
      <h3 :class="titleClass">
        <span v-if="variant === 'fullscreen'" class="fullscreen-form-section__indicator" />
        {{ t('protection.policiesPage.tabBasic') }}
      </h3>
      <div class="policy-basic-grid">
        <div data-validation-field="name" class="policy-basic-row">
          <label class="policy-basic-row__label" for="policy-name-input">
            {{ t('protection.policiesPage.labelPolicyName') }}
            <span class="policy-basic-row__required">*</span>
          </label>
          <div class="policy-basic-row__control" :class="{ 'is-invalid': !!nameError }">
            <ElInput
              id="policy-name-input"
              v-model="policyForm.name"
              :placeholder="t('protection.policiesPage.phPolicyName')"
              maxlength="128"
              show-word-limit
              @input="$emit('clear-name-error')"
            />
            <p v-if="nameError" class="fullscreen-form-inline-error">{{ nameError }}</p>
          </div>
        </div>
        <div class="policy-basic-row">
          <span class="policy-basic-row__label">{{ t('protection.policiesPage.labelPolicyEnabled') }}</span>
          <div class="policy-basic-row__control policy-basic-row__control--switch">
            <el-switch v-model="policyForm.policyActive" />
          </div>
        </div>
      </div>
    </section>

    <section data-validation-field="schedule" :class="nestedSectionClass">
      <div class="policy-section-head">
        <el-checkbox v-model="policyForm.sectionScheduleEnabled" />
        <span class="policy-section-head__title">{{ t('protection.policiesPage.sectionCheckSchedule') }}</span>
      </div>
      <p v-if="!policyForm.sectionScheduleEnabled" class="policy-section-off-hint">{{ t('protection.policiesPage.sectionOffHint') }}</p>
      <p v-if="scheduleError" class="fullscreen-form-inline-error">{{ scheduleError }}</p>
      <div v-if="policyForm.sectionScheduleEnabled" class="policy-section-nested">
        <el-radio-group v-model="policyForm.freqMode" class="mb-3">
          <el-radio value="simple">{{ t('protection.policiesPage.freqSimple') }}</el-radio>
          <el-radio value="advanced">{{ t('protection.policiesPage.freqAdvanced') }}</el-radio>
        </el-radio-group>
        <div class="schedule-context-grid">
          <ElFormItem :label="t('protection.policiesPage.scheduleTimezone')" class="!mb-0">
            <el-select
              v-model="policyForm.scheduleTimezone"
              filterable
              class="schedule-context-control"
              :placeholder="t('protection.policiesPage.scheduleTimezonePlaceholder')"
            >
              <el-option
                v-for="timezone in scheduleTimezoneOptions"
                :key="timezone.value"
                :label="timezone.label"
                :value="timezone.value"
              />
            </el-select>
          </ElFormItem>
          <ElFormItem :label="t('protection.policiesPage.scheduleStartsAt')" class="!mb-0">
            <ElDatePicker
              v-model="policyForm.scheduleStartsAt"
              type="datetime"
              class="schedule-context-control"
              format="YYYY-MM-DD HH:mm"
              value-format="YYYY-MM-DDTHH:mm"
              :placeholder="t('protection.policiesPage.scheduleStartsAtPlaceholder')"
            />
          </ElFormItem>
        </div>
        <p class="schedule-context-hint">{{ t('protection.policiesPage.scheduleTimezoneHint') }}</p>

        <div v-if="policyForm.freqMode === 'simple'" class="quick-schedule-stack">
          <ElFormItem :label="t('protection.policiesPage.scheduleCycle')" class="!mb-0">
            <el-select v-model="policyForm.quickScheduleType" class="schedule-cycle-control">
              <el-option
                v-for="option in quickScheduleTypeOptions"
                :key="option.value"
                :label="option.label"
                :value="option.value"
              />
            </el-select>
          </ElFormItem>

          <div v-if="policyForm.quickScheduleType === 'interval'" class="schedule-inline-controls">
            <ElFormItem :label="t('protection.policiesPage.labelInterval')" class="!mb-0">
              <el-select
                v-model="policyForm.simpleIntervalUnit"
                class="schedule-interval-unit"
                @change="onSimpleIntervalUnitChange"
              >
                <el-option
                  v-for="opt in simpleIntervalOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </ElFormItem>
            <ElFormItem :label="currentIntervalUnitMeta.valueLabel + t('protection.policiesPage.labelColon')" class="!mb-0">
              <ElInputNumber
                v-model="policyForm.simpleIntervalValue"
                :min="currentIntervalUnitMeta.min"
                :max="currentIntervalUnitMeta.max"
                :step="1"
                step-strictly
              />
            </ElFormItem>
          </div>

          <template v-else>
            <div v-if="policyForm.quickScheduleType === 'weekly'" class="schedule-picker-block">
              <span class="schedule-picker-label">{{ t('protection.policiesPage.scheduleWeekdays') }}</span>
              <ElCheckboxGroup v-model="policyForm.scheduleWeekdays" class="schedule-weekday-grid">
                <ElCheckbox
                  v-for="option in scheduleWeekdayOptions"
                  :key="option.value"
                  :value="option.value"
                >
                  {{ option.label }}
                </ElCheckbox>
              </ElCheckboxGroup>
            </div>

            <div v-if="policyForm.quickScheduleType === 'monthly'" class="schedule-picker-block">
              <span class="schedule-picker-label">{{ t('protection.policiesPage.scheduleMonthDays') }}</span>
              <div class="schedule-month-day-grid">
                <button
                  v-for="day in scheduleMonthDayOptions"
                  :key="day"
                  type="button"
                  class="schedule-month-day"
                  :class="{ 'schedule-month-day--selected': policyForm.scheduleMonthDays.includes(day) }"
                  :aria-pressed="policyForm.scheduleMonthDays.includes(day)"
                  @click="toggleScheduleMonthDay(day)"
                >
                  {{ day }}
                </button>
                <button
                  type="button"
                  class="schedule-month-day schedule-month-day--end"
                  :class="{ 'schedule-month-day--selected': policyForm.scheduleMonthEnd }"
                  :aria-pressed="policyForm.scheduleMonthEnd"
                  @click="policyForm.scheduleMonthEnd = !policyForm.scheduleMonthEnd"
                >
                  {{ t('protection.policiesPage.scheduleMonthEnd') }}
                </button>
              </div>
            </div>

            <ElFormItem :label="t('protection.policiesPage.scheduleTime')" class="schedule-time-field !mb-0">
              <ElInput
                v-model="policyForm.scheduleTime"
                type="time"
                class="schedule-time-control"
              />
            </ElFormItem>
          </template>
          <p v-if="scheduleErrorText" class="schedule-field-error">{{ scheduleErrorText }}</p>
        </div>
        <div v-else class="space-y-3">
          <div class="cron-row">
            <label class="cron-row__label" :for="cronInputId">
              {{ t('protection.policiesPage.cronLabel') }}<span class="cron-row__required">*</span>
            </label>
            <div class="cron-row__field">
              <ElInput
                :id="cronInputId"
                v-model="policyForm.cronExpr"
                :placeholder="t('protection.policiesPage.cronPh')"
                :class="['cron-input', { 'cron-input--error': policyForm.sectionScheduleEnabled && !cronValidation.ok }]"
              />
              <p v-if="cronErrorText && policyForm.sectionScheduleEnabled" class="cron-row__error">{{ cronErrorText }}</p>
            </div>
          </div>

          <div v-if="showCronHelp" class="policy-cron-help text-slate-600 bg-[var(--color-grey-1,#f8fafc)] border border-[var(--color-border,#e2e8f0)] rounded-[var(--radius-card)] px-3 py-2 space-y-2">
            <p class="m-0">{{ t('protection.policiesPage.cronHelpLead') }}</p>
            <div>
              <p class="m-0 font-medium text-slate-700">{{ t('protection.policiesPage.cronHelpSymbolsTitle') }}</p>
              <ul class="list-disc pl-5 m-0">
                <li><code>*</code>{{ t('protection.policiesPage.cronSymStar') }}</li>
                <li><code>,</code>{{ t('protection.policiesPage.cronSymComma') }}</li>
                <li><code>-</code>{{ t('protection.policiesPage.cronSymDash') }}</li>
                <li><code>/</code>{{ t('protection.policiesPage.cronSymSlash') }}</li>
              </ul>
            </div>
            <div>
              <p class="m-0 font-medium text-slate-700">{{ t('protection.policiesPage.cronExamplesTitle') }}</p>
              <ul class="list-disc pl-5 m-0">
                <li><code>15 * * * *</code>{{ t('protection.policiesPage.cronEx15') }}</li>
                <li><code>0 2 * * *</code>{{ t('protection.policiesPage.cronEx0200') }}</li>
                <li><code>0 0 * * 1</code>{{ t('protection.policiesPage.cronExMon') }}</li>
                <li><code>0 5 1 * *</code>{{ t('protection.policiesPage.cronExMonth1') }}</li>
                <li><code>*/5 * * * *</code>{{ t('protection.policiesPage.cronExEvery5') }}</li>
                <li><code>*/5 8-20 * * *</code>{{ t('protection.policiesPage.cronExEvery5Window') }}</li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section data-validation-field="retention" :class="nestedSectionClass">
      <div class="policy-section-head">
        <el-checkbox v-model="policyForm.sectionRetentionEnabled" />
        <span class="policy-section-head__title">{{ t('protection.policiesPage.sectionCheckRetention') }}</span>
      </div>
      <p v-if="!policyForm.sectionRetentionEnabled" class="policy-section-off-hint">{{ t('protection.policiesPage.sectionOffHint') }}</p>
      <p v-if="retentionError" class="fullscreen-form-inline-error">{{ retentionError }}</p>
      <div v-if="policyForm.sectionRetentionEnabled" class="policy-section-nested policy-retention-stack">
        <div>
          <div class="policy-option-title policy-option-title--with-desc">
            {{ t('protection.policiesPage.recentTitle') }}
            <span class="policy-inline-desc">
              {{ t('protection.policiesPage.recentSub', { n: policyForm.retentionRecentPoints }) }}
            </span>
          </div>
          <div class="retention-recent-row">
            <span class="policy-inline-label">{{ t('protection.policiesPage.recentLine') }}</span>
            <div :class="[retentionInputWrapClass, { 'is-invalid': !!recentRetentionError }]">
              <ElInputNumber v-model="policyForm.retentionRecentPoints" :min="1" :max="9999" />
              <p v-if="recentRetentionError" class="fullscreen-form-inline-error retention-recent-input-wrap__error">
                {{ recentRetentionError }}
              </p>
            </div>
            <span class="policy-inline-label">{{ t('protection.policiesPage.recentPoints') }}</span>
          </div>
        </div>
        <el-divider class="!my-2" />
        <div>
          <div v-if="variant === 'fullscreen'" class="policy-option-title policy-option-title--with-desc">
            {{ t('protection.policiesPage.shortTitle') }}
            <span v-if="policyForm.retentionShortHourly" class="policy-inline-desc">
              {{ t('protection.policiesPage.shortDesc', { days: policyForm.retentionShortDaysMax }) }}
            </span>
          </div>
          <div class="retention-tier-row">
            <el-switch
              :model-value="policyForm.retentionShortHourly"
              @update:model-value="toggleHourlyRetention"
            />
            <div :class="['retention-tier-input-wrap', { 'is-invalid': !!hourlyRetentionError }]">
              <ElInputNumber
                v-model="policyForm.retentionShortDaysMax"
                :min="1"
                :max="30"
                :disabled="!policyForm.retentionShortHourly"
              />
              <p v-if="hourlyRetentionError" class="fullscreen-form-inline-error retention-input-wrap__error">
                {{ hourlyRetentionError }}
              </p>
            </div>
            <span class="retention-tier-unit policy-inline-label">{{ t('protection.policiesPage.unitDays') }}</span>
          </div>
        </div>
        <div>
          <div v-if="variant === 'fullscreen'" class="policy-option-title policy-option-title--with-desc">
            {{ t('protection.policiesPage.midTitle') }}
            <span v-if="policyForm.retentionMidDaily" class="policy-inline-desc">
              {{ t('protection.policiesPage.midDesc', { mid: policyForm.retentionMidDaysMax }) }}
            </span>
          </div>
          <div class="retention-tier-row">
            <el-switch
              :model-value="policyForm.retentionMidDaily"
              @update:model-value="toggleDailyRetention"
            />
            <div :class="['retention-tier-input-wrap', { 'is-invalid': !!dailyRetentionError || !!midRetentionError }]">
              <ElInputNumber
                v-model="policyForm.retentionMidDaysMax"
                :min="1"
                :max="365"
                :disabled="!policyForm.retentionMidDaily"
              />
              <p v-if="dailyRetentionError" class="fullscreen-form-inline-error retention-input-wrap__error">
                {{ dailyRetentionError }}
              </p>
            </div>
            <span class="retention-tier-unit policy-inline-label">{{ t('protection.policiesPage.unitDays') }}</span>
          </div>
          <p v-if="midRetentionError" class="text-[13px] text-red-500 m-0 mt-1">{{ midRetentionError }}</p>
        </div>
        <div>
          <div v-if="variant === 'fullscreen'" class="policy-option-title policy-option-title--with-desc">
            {{ t('protection.policiesPage.longTitle') }}
            <span v-if="policyForm.retentionLongMonthly" class="policy-inline-desc">
              {{ t('protection.policiesPage.longDesc', { months: policyForm.retentionLongMonths }) }}
            </span>
          </div>
          <div class="retention-tier-row">
            <el-switch
              :model-value="policyForm.retentionLongMonthly"
              @update:model-value="toggleMonthlyRetention"
            />
            <div :class="['retention-tier-input-wrap', { 'is-invalid': !!monthlyRetentionError || !!longVsShortRetentionError }]">
              <ElInputNumber
                v-model="policyForm.retentionLongMonths"
                :min="1"
                :max="120"
                :disabled="!policyForm.retentionLongMonthly"
              />
              <p v-if="monthlyRetentionError" class="fullscreen-form-inline-error retention-input-wrap__error">
                {{ monthlyRetentionError }}
              </p>
            </div>
            <span class="retention-tier-unit policy-inline-label">{{ t('protection.policiesPage.unitMonths') }}</span>
          </div>
          <p v-if="longVsShortRetentionError" class="text-[13px] text-red-500 m-0 mt-1">{{ longVsShortRetentionError }}</p>
        </div>
      </div>
    </section>

    <section :class="nestedSectionClass">
      <h3 :class="titleClass">
        <span v-if="variant === 'fullscreen'" class="fullscreen-form-section__indicator" />
        {{ t('protection.policiesPage.sectionAdvancedSettings') }}
      </h3>
      <div class="policy-section-nested advanced-settings-panel">
        <div class="error-policy-list">
          <div v-for="row in errorHandlingRows" :key="row.key" class="error-policy-list__item">
            <el-switch v-model="policyForm[row.key]" class="error-policy-list__switch" />
            <div class="error-policy-list__main">
              <div class="error-policy-list__title">{{ row.title }}</div>
              <p class="error-policy-list__desc">{{ row.desc }}</p>
            </div>
          </div>
        </div>
      </div>
    </section>
  </ElForm>

  <ElForm
    v-show="showFilter"
    :model="filterForm"
    label-position="top"
    :class="filterFormClass"
  >
    <section :class="sectionClass">
      <h3 :class="titleClass">
        <span v-if="variant === 'fullscreen'" class="fullscreen-form-section__indicator" />
        {{ t('protection.policiesPage.tabBasic') }}
      </h3>
      <div class="policy-basic-grid">
        <div data-validation-field="name" class="policy-basic-row">
          <label class="policy-basic-row__label" for="filter-rule-name-input">
            {{ t('protection.policiesPage.fieldFilterRuleName') }}
            <span class="policy-basic-row__required">*</span>
          </label>
          <div class="policy-basic-row__control" :class="{ 'is-invalid': !!nameError }">
            <ElInput
              id="filter-rule-name-input"
              v-model="filterForm.name"
              :placeholder="t('protection.policiesPage.phFilterName')"
              maxlength="128"
              show-word-limit
              @input="$emit('clear-name-error')"
            />
            <p v-if="nameError" class="fullscreen-form-inline-error">{{ nameError }}</p>
          </div>
        </div>
        <div class="policy-basic-row">
          <span class="policy-basic-row__label">{{ t('protection.policiesPage.labelPolicyEnabled') }}</span>
          <div class="policy-basic-row__control policy-basic-row__control--switch">
            <el-switch v-model="filterForm.policyActive" />
          </div>
        </div>
      </div>
    </section>
    <section :class="[sectionClass, { 'policy-dialog-card--rules': variant === 'dialog' }]">
      <div class="filter-section-heading">
        <h3 :class="titleClass">
          <span v-if="variant === 'fullscreen'" class="fullscreen-form-section__indicator" />
          {{ t('protection.policiesPage.filterRulesSectionTitle') }}
        </h3>
        <button type="button" class="filter-rule-guide-button" @click="openFilterRuleGuide">
          <BookOpen :size="14" />
          <span>{{ t('protection.policiesPage.filterRuleGuideBtn') }}</span>
        </button>
      </div>
      <div class="filter-rules-stack">
        <div class="filter-rules-subsection">
          <div class="policy-option-title">{{ t('protection.policiesPage.filterPresetSectionTitle') }}</div>
          <div
            class="filter-preset-grid"
            role="group"
            :aria-label="t('protection.policiesPage.filterPresetSectionTitle')"
          >
            <label
              v-for="preset in filterPresets"
              :key="preset.key"
              class="filter-preset-card"
              :class="{ 'filter-preset-card--active': filterForm[preset.key] }"
            >
              <el-checkbox v-model="filterForm[preset.key]" @change="onFilterPresetChange" />
              <span class="filter-preset-card__body">
                <span class="filter-preset-card__title">{{ preset.title }}</span>
                <span class="filter-preset-card__desc">{{ preset.desc }}</span>
              </span>
            </label>
          </div>
        </div>

        <div class="filter-rules-subsection">
          <div class="policy-option-title">{{ t('protection.policiesPage.filterCustomSectionTitle') }}</div>
          <div class="filter-custom-block">
            <div class="filter-rule-group">
              <div class="filter-custom-head">
                <div>
                  <div class="filter-rule-group__title">{{ t('protection.policiesPage.filterExcludeRulesTitle') }}</div>
                  <p class="filter-rule-group__desc">{{ t('protection.policiesPage.filterExcludeRulesDesc') }}</p>
                </div>
                <button
                  type="button"
                  class="filter-custom-rule-text-toggle"
                  @click="setFilterRulesTextMode(!filterRulesTextMode)"
                >
                  {{ filterRulesTextMode ? t('protection.policiesPage.filterEditAsRows') : t('protection.policiesPage.filterEditAsText') }}
                </button>
              </div>
              <div class="filter-rule-group__notice">
                <TriangleAlert :size="15" />
                <span>{{ t('protection.policiesPage.filterRuleOrderHint') }}</span>
              </div>
              <div v-if="!filterRulesTextMode" class="filter-custom-rules">
                <div
                  v-for="item in filterExcludeRules"
                  :key="`exclude-${item.index}`"
                  class="filter-custom-rule-row"
                >
                  <ElInput
                    :model-value="item.rule.pattern"
                    size="small"
                    class="filter-custom-rule-row__pattern"
                    :placeholder="t('protection.policiesPage.filterExcludePatternPh')"
                    @input="(value) => setFilterCustomRulePattern(item.index, value)"
                    @paste="(event) => onFilterCustomRulePaste(item.index, event)"
                  />
                  <button
                    type="button"
                    class="filter-custom-rule-row__remove"
                    :aria-label="t('protection.policiesPage.filterDeleteExcludeRule')"
                    @click="removeFilterCustomRule(item.index)"
                  >
                    <Trash2 :size="14" />
                  </button>
                </div>
                <p v-if="!filterExcludeRules.length" class="filter-rule-group__empty">
                  {{ t('protection.policiesPage.filterNoExcludeRules') }}
                </p>
                <div class="filter-custom-rule-add-wrap">
                  <button
                    type="button"
                    class="filter-custom-rule-add filter-custom-rule-add--row"
                    @click="addFilterExcludeRule"
                  >
                    <Plus :size="16" />
                    <span>{{ t('protection.policiesPage.filterAddExcludeRule') }}</span>
                  </button>
                </div>
              </div>
              <div v-else class="filter-custom-text-mode">
                <ElInput
                  :model-value="filterRulesTextValue"
                  type="textarea"
                  :rows="8"
                  resize="vertical"
                  class="filter-custom-text-mode__textarea"
                  :placeholder="t('protection.policiesPage.filterTextModePlaceholder')"
                  @input="(value) => onFilterRulesTextInput(String(value))"
                />
                <div class="filter-custom-text-mode__meta">
                  {{ t('protection.policiesPage.filterRuleCount', { n: filterTextRuleCount }) }}
                </div>
              </div>
            </div>

            <p v-if="hasDuplicateFilterRules" class="filter-rule-group__warning">
              {{ t('protection.policiesPage.filterDuplicateWarning') }}
            </p>
          </div>
        </div>

        <div class="filter-rules-subsection">
          <div class="policy-option-title">{{ t('protection.policiesPage.filterFinalPreviewTitle') }}</div>
          <div class="filter-rules-preview">
            <template v-if="compiledFilterRuleLines.length">
              <div class="filter-rules-preview__group">
                <div class="filter-rules-preview__divider">{{ t('protection.policiesPage.filterExcludeRulesTitle') }}</div>
                <code
                  v-for="line in compiledFilterRuleLines"
                  :key="line"
                  class="filter-rules-preview__line"
                >
                  {{ line }}
                </code>
              </div>
            </template>
            <p v-if="!compiledFilterRuleLines.length" class="filter-rule-group__empty">
              {{ t('protection.policiesPage.filterNoActiveRules') }}
            </p>
          </div>
        </div>
      </div>
    </section>

    <section :class="sectionClass">
      <h3 :class="titleClass">
        <span v-if="variant === 'fullscreen'" class="fullscreen-form-section__indicator" />
        {{ t('protection.policiesPage.sectionAdvancedSettings') }}
      </h3>
      <div class="filter-advanced-list">
        <div class="filter-advanced-row filter-advanced-row--large-file">
          <el-switch v-model="filterForm.largeFileLimitEnabled" class="filter-advanced-row__switch" />
          <div class="filter-advanced-row__main">
            <div class="filter-advanced-row__title">{{ t('protection.policiesPage.largeTitle') }}</div>
            <p class="filter-advanced-row__desc">{{ t('protection.policiesPage.largeFileLimitEnableSub') }}</p>
          </div>
          <div class="filter-advanced-row__controls">
            <span class="policy-inline-label">{{ t('protection.policiesPage.largeLine') }}</span>
            <ElInputNumber
              v-model="filterForm.largeFileMax"
              :disabled="!filterForm.largeFileLimitEnabled"
              :min="1"
              :max="999999"
              class="filter-advanced-row__number"
            />
            <el-select
              v-model="filterForm.largeFileUnit"
              class="filter-large-file-unit"
              :disabled="!filterForm.largeFileLimitEnabled"
            >
              <el-option label="KB" value="KB" />
              <el-option label="MB" value="MB" />
              <el-option label="GB" value="GB" />
            </el-select>
          </div>
        </div>

        <div class="filter-advanced-row">
          <el-switch v-model="filterForm.ignoreCacheDirectories" class="filter-advanced-row__switch" />
          <div class="filter-advanced-row__main">
            <div class="filter-advanced-row__title">{{ t('protection.policiesPage.cacheTitle') }}</div>
            <p class="filter-advanced-row__desc">{{ t('protection.policiesPage.cacheSub') }}</p>
          </div>
        </div>

        <div class="filter-advanced-row filter-advanced-row--last">
          <el-switch v-model="filterForm.currentFilesystemOnly" class="filter-advanced-row__switch" />
          <div class="filter-advanced-row__main">
            <div class="filter-advanced-row__title">
              {{ t('protection.policiesPage.fsOnlyTitle') }}
              <HflHelpTip
                :content="t('protection.policiesPage.fsOnlyTooltip')"
                :aria-label="t('protection.policiesPage.fsOnlyTitle')"
              />
            </div>
            <p class="filter-advanced-row__desc">{{ t('protection.policiesPage.fsOnlySub') }}</p>
          </div>
        </div>
      </div>
    </section>
  </ElForm>
</template>

<style scoped>
.policy-section-nested {
  margin-top: 8px;
  padding: 18px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.94) 0%, rgba(248, 250, 252, 0.92) 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.78);
}

:deep(.el-switch) {
  --el-switch-on-color: var(--color-primary, var(--el-color-primary, #409eff));
  --el-switch-off-color: var(--color-border, var(--el-border-color, #dcdfe6));
  height: 22px;
  line-height: 22px;
}

:deep(.el-switch__core) {
  min-width: 40px;
  width: 40px;
  height: 20px;
  border-radius: 999px;
  box-shadow: inset 0 1px 2px rgba(15, 23, 42, 0.14);
  transition: border-color 0.16s ease, background-color 0.16s ease, box-shadow 0.16s ease;
}

:deep(.el-switch__core .el-switch__action) {
  left: 2px;
  width: 16px;
  height: 16px;
  border-radius: 999px;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.22);
}

:deep(.el-switch.is-checked .el-switch__core .el-switch__action) {
  left: calc(100% - 18px);
}

:deep(.el-switch__label) {
  font-size: 12px;
  line-height: 1;
}

.policy-basic-grid {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.policy-basic-row {
  display: grid;
  grid-template-columns: minmax(124px, 148px) minmax(0, 1fr);
  align-items: center;
  gap: 12px;
}

.policy-basic-row__label {
  color: rgb(51 65 85);
  font-size: 13px;
  font-weight: 650;
  line-height: 1.45;
}

.policy-basic-row__required {
  margin-left: 3px;
  color: rgb(239 68 68);
}

.policy-basic-row__control {
  position: relative;
  min-width: 0;
}

.policy-basic-row__control.is-invalid :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.policy-basic-row__control .fullscreen-form-inline-error {
  position: absolute;
  margin-top: 2px;
  left: 0;
  z-index: 1;
}

.policy-basic-row__control--switch {
  display: flex;
  align-items: center;
  min-height: 32px;
}

.cron-row {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-start;
  gap: 8px 12px;
}

.cron-row__label {
  flex-shrink: 0;
  color: rgb(51 65 85);
  font-size: 13px;
  font-weight: 650;
  line-height: 32px;
  white-space: nowrap;
}

.cron-row__required {
  margin-left: 3px;
  color: var(--el-color-danger);
}

.cron-row__field {
  flex: 1 1 320px;
  min-width: 0;
  max-width: 460px;
}

.cron-input--error :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.cron-row__error {
  margin: 4px 0 0;
  color: var(--el-color-danger);
  font-size: 12px;
  line-height: 1.35;
}

.policy-cron-help {
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.96) 0%, rgba(241, 245, 249, 0.92) 100%);
  border-color: rgba(226, 232, 240, 0.95);
}

.schedule-context-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 6px;
}

.schedule-context-control,
.schedule-cycle-control {
  width: 100%;
}

.schedule-context-hint {
  margin: 0 0 14px;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.5;
}

.quick-schedule-stack {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.schedule-cycle-control {
  max-width: 240px;
}

.schedule-inline-controls {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 12px;
}

.schedule-interval-unit {
  width: 240px !important;
}

.schedule-picker-block {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.schedule-picker-label {
  color: rgb(51 65 85);
  font-size: 13px;
  font-weight: 650;
}

.schedule-weekday-grid {
  display: grid;
  grid-template-columns: repeat(7, minmax(0, 1fr));
  gap: 8px;
}

.schedule-weekday-grid :deep(.el-checkbox) {
  margin-right: 0;
}

.schedule-month-day-grid {
  display: grid;
  grid-template-columns: repeat(8, minmax(36px, 1fr));
  gap: 6px;
}

.schedule-month-day {
  min-height: 34px;
  padding: 4px 6px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 6px;
  background: rgba(255, 255, 255, 0.96);
  color: rgb(51 65 85);
  cursor: pointer;
  transition: border-color 0.15s ease, background-color 0.15s ease, color 0.15s ease;
}

.schedule-month-day:hover {
  border-color: var(--color-primary, var(--el-color-primary, #409eff));
}

.schedule-month-day--selected {
  border-color: var(--color-primary, var(--el-color-primary, #409eff));
  background: var(--color-primary, var(--el-color-primary, #409eff));
  color: #fff;
}

.schedule-month-day--end {
  grid-column: span 2;
}

.schedule-time-field {
  max-width: 240px;
}

.schedule-time-control {
  width: 100%;
}

.schedule-field-error {
  margin: -4px 0 0;
  color: var(--el-color-danger, #f56c6c);
  font-size: 12px;
  line-height: 1.4;
}

.policy-option-title {
  margin: 0 0 6px;
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.45;
}

.policy-option-title--with-desc {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.policy-option-desc {
  margin: 2px 0 10px;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.55;
}

.policy-option-desc--lead {
  margin-bottom: 12px;
}

.policy-inline-label {
  color: rgb(51 65 85);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.45;
}

.policy-inline-desc {
  color: rgb(100 116 139);
  font-size: 12px;
  font-weight: 400;
  line-height: 1.5;
}


.policy-retention-stack {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.advanced-settings-panel {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.advanced-setting-row {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr) minmax(240px, 320px);
  align-items: center;
  gap: 12px;
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(226, 232, 240, 0.95);
}

.advanced-setting-row__switch {
  display: flex;
  justify-content: flex-start;
}

.advanced-setting-row__main {
  min-width: 0;
}

.advanced-setting-row__title {
  color: rgb(15 23 42);
  font-size: 14px;
  font-weight: 650;
  line-height: 1.45;
}

.advanced-setting-row__desc {
  margin: 2px 0 0;
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.5;
}

.advanced-setting-row__controls {
  display: grid;
  grid-template-columns: max-content minmax(96px, 1fr) max-content;
  align-items: center;
  gap: 8px;
  justify-self: end;
  width: 100%;
}

.advanced-setting-row__prefix,
.advanced-setting-row__unit {
  color: rgb(51 65 85);
  font-size: 13px;
  white-space: nowrap;
}

.advanced-setting-row__number {
  width: 100%;
}

.error-policy-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.error-policy-list__item {
  display: grid;
  grid-template-columns: 44px minmax(0, 1fr);
  align-items: start;
  gap: 12px;
}

.error-policy-list__switch {
  justify-self: start;
}

.error-policy-list__main {
  min-width: 0;
}

.retention-recent-row {
  display: grid;
  grid-template-columns: max-content 140px max-content;
  align-items: center;
  gap: 8px;
}

.retention-tier-row {
  display: grid;
  grid-template-columns: 40px 140px max-content;
  align-items: center;
  gap: 10px 12px;
}

.retention-tier-row :deep(.el-switch) {
  min-width: 40px;
  justify-self: start;
  white-space: nowrap;
}

.retention-tier-label,
.retention-tier-unit {
  white-space: nowrap;
}

.retention-tier-input-wrap,
.retention-recent-input-wrap {
  position: relative;
  width: 140px;
}

.retention-recent-input-wrap.is-invalid :deep(.el-input__wrapper),
.retention-tier-input-wrap.is-invalid :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

.retention-recent-input-wrap__error,
.retention-input-wrap__error {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  width: max-content;
  margin: 0;
}

.retention-tier-input-wrap :deep(.el-input-number),
.retention-recent-input-wrap :deep(.el-input-number) {
  width: 100%;
}

.toggle-row__title,
.error-policy-list__title {
  color: rgb(15 23 42);
  font-size: 13px;
  font-weight: 650;
  line-height: 1.45;
}

.toggle-row__sub,
.error-policy-list__desc {
  color: rgb(100 116 139);
  font-size: 12px;
  line-height: 1.55;
}

@media (max-width: 640px) {
  .schedule-context-grid {
    grid-template-columns: 1fr;
  }

  .schedule-cycle-control,
  .schedule-interval-unit,
  .schedule-time-field {
    max-width: none;
    width: 100%;
  }

  .schedule-weekday-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .schedule-month-day-grid {
    grid-template-columns: repeat(5, minmax(36px, 1fr));
  }

  .policy-basic-row {
    grid-template-columns: 1fr;
    justify-items: stretch;
  }

  .policy-basic-row__control {
    width: 100%;
    justify-self: stretch;
  }

  .policy-basic-row__control--switch {
    width: auto;
    justify-self: start;
  }

  .advanced-setting-row,
  .retention-recent-row,
  .retention-tier-row {
    grid-template-columns: 1fr;
    justify-items: start;
  }

  .advanced-setting-row__controls {
    justify-self: stretch;
  }

  .retention-tier-input-wrap,
  .retention-recent-input-wrap {
    width: min(100%, 200px);
  }
}
</style>

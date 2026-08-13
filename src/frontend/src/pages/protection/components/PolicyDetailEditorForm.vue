<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatLocalDateTime } from '../../../lib/dateTime'
import {
  getSimpleIntervalUnitMeta,
  formatScheduleStartForDisplay,
  humanizeCronExpression,
  summarizeSchedule,
  type BackupPolicyForm,
  type MessageLocale,
} from '../../../lib/protectionPolicyFormModel'
import { booleanStatusTag } from '../../../lib/statusTag'

const props = defineProps<{
  policyForm: BackupPolicyForm
  createdAt?: string
  associatedSourceCount: number
  updatedAt?: string
  hideInfoSection?: boolean
}>()

const { t, locale } = useI18n()
const messageLocale = computed<MessageLocale>(() => 'en')
const emptyText = computed(() => t('protection.policiesPage.timeDash'))
const statusOnText = computed(() => t('protection.policiesPage.switchEnabledOn'))
const statusOffText = computed(() => t('protection.policiesPage.switchEnabledOff'))
const notConfiguredText = computed(() => 'Not configured')

const scheduleSummary = computed(() => summarizeSchedule(props.policyForm, messageLocale.value))

const quickScheduleLabel = computed(() => {
  const f = props.policyForm
  if (f.quickScheduleType === 'interval') {
    const unit = getSimpleIntervalUnitMeta(f.simpleIntervalUnit, messageLocale.value)
    return `Every ${f.simpleIntervalValue} ${Number(f.simpleIntervalValue) === 1 ? unit.unitText.replace(/s$/, '') : unit.unitText}`
  }
  if (f.quickScheduleType === 'daily') return `Daily at ${f.scheduleTime}`
  if (f.quickScheduleType === 'weekly') {
    const names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    const weekdays = f.scheduleWeekdays.map((day) => names[day - 1]).join(', ')
    return `Weekly on ${weekdays} at ${f.scheduleTime}`
  }
  const dates = f.scheduleMonthDays.map(String)
  if (f.scheduleMonthEnd) dates.push('E.O.M')
  return `Monthly on ${dates.join(', ')} at ${f.scheduleTime}`
})

const cronDescription = computed(() =>
  props.policyForm.sectionScheduleEnabled
    ? humanizeCronExpression(props.policyForm.cronExpr, messageLocale.value)
    : notConfiguredText.value,
)

const retentionDetailLines = computed(() => {
  const f = props.policyForm
  if (!f.sectionRetentionEnabled) return []
  const latestSuffix = Number(f.retentionRecentPoints) === 1 ? 'point' : 'points'
  const lines = [
    {
      label: '',
      text: `Keep the latest ${f.retentionRecentPoints} restore ${latestSuffix}.`,
    },
  ]
  if (f.retentionShortHourly) {
    lines.push({
      label: 'Hourly:',
      text: `First ${f.retentionShortDaysMax} days, one restore point per hour.`,
    })
  }
  if (f.retentionMidDaily) {
    lines.push({
      label: 'Daily:',
      text: `Day ${f.retentionShortDaysMax} to day ${f.retentionMidDaysMax}, one restore point per day.`,
    })
  }
  if (f.retentionLongMonthly) {
    lines.push({
      label: 'Monthly:',
      text: `After day ${f.retentionMidDaysMax}, one restore point per month, up to ${f.retentionLongMonths} months.`,
    })
  }
  return [
    ...lines,
  ]
})

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

function fmtTime(value: string | null | undefined) {
  return formatLocalDateTime(value, emptyText.value, locale.value)
}

function enabledText(enabled: boolean) {
  return enabled ? statusOnText.value : statusOffText.value
}

</script>

<template>
  <div class="hfl-detail-sections policy-detail-overview">
    <section v-if="!hideInfoSection" class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">{{ t('protection.policiesPage.sectionPolicyInfo') }}</h4>
      <div class="hfl-detail-grid">
        <div class="policy-detail-editor__pair-row">
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldName') }}</span>
            <span class="hfl-detail-row__value policy-detail-overview__name-value">
              <span class="hfl-detail-row__text policy-detail-overview__primary">{{ policyForm.name || emptyText }}</span>
              <ElTag :type="booleanStatusTag(policyForm.policyActive).type" :class="booleanStatusTag(policyForm.policyActive).class" size="small">{{ enabledText(policyForm.policyActive) }}</ElTag>
            </span>
          </div>
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldRelatedBackupSources') }}</span>
            <span class="hfl-detail-row__value">
              <span class="policy-detail-record__count">{{ associatedSourceCount }}</span>
            </span>
          </div>
        </div>
        <div class="policy-detail-editor__pair-row">
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldCreatedAt') }}</span>
            <span class="hfl-detail-row__value">{{ fmtTime(createdAt) }}</span>
          </div>
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldUpdatedAt') }}</span>
            <span class="hfl-detail-row__value">{{ fmtTime(updatedAt) }}</span>
          </div>
        </div>
      </div>
    </section>

    <section class="hfl-detail-section" :class="{ 'policy-detail-overview__section--off': !policyForm.sectionScheduleEnabled }">
      <h4 class="hfl-detail-section__title">{{ t('protection.policiesPage.sectionSchedule') }}</h4>
      <div class="hfl-detail-grid">
        <div v-if="!policyForm.sectionScheduleEnabled" class="hfl-detail-row hfl-detail-row--full">
          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldSchedule') }}</span>
          <span class="hfl-detail-row__value">{{ notConfiguredText }}</span>
        </div>
        <div v-else class="policy-detail-editor__pair-row">
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.labelFreqMode') }}</span>
            <span class="hfl-detail-row__value">
              <ElTag size="small">{{ policyForm.freqMode === 'advanced' ? t('protection.policiesPage.freqAdvanced') : t('protection.policiesPage.freqSimple') }}</ElTag>
            </span>
          </div>
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ policyForm.freqMode === 'advanced' ? t('protection.policiesPage.cronLabel') : t('protection.policiesPage.scheduleCycle') }}</span>
            <span
              class="hfl-detail-row__value"
              :class="{ 'hfl-detail-row__value--stacked': policyForm.freqMode === 'advanced' }"
            >
              <code v-if="policyForm.freqMode === 'advanced'" class="policy-detail-overview__code">{{ policyForm.cronExpr }}</code>
              <span v-else class="hfl-detail-row__text">{{ quickScheduleLabel }}</span>
              <span v-if="policyForm.freqMode === 'advanced'" class="hfl-detail-row__hint">{{ cronDescription }}</span>
            </span>
          </div>
        </div>
        <div v-if="policyForm.sectionScheduleEnabled" class="policy-detail-editor__pair-row">
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.scheduleTimezone') }}</span>
            <span class="hfl-detail-row__value">{{ policyForm.scheduleTimezone || 'UTC' }}</span>
          </div>
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.scheduleStartsAt') }}</span>
            <span class="hfl-detail-row__value" :class="{ 'hfl-detail-row__empty': !policyForm.scheduleStartsAt }">{{ formatScheduleStartForDisplay(policyForm.scheduleStartsAt, emptyText) }}</span>
          </div>
        </div>
        <div v-if="policyForm.sectionScheduleEnabled" class="hfl-detail-row hfl-detail-row--full">
          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldSchedule') }}</span>
          <span class="hfl-detail-row__value">{{ scheduleSummary }}</span>
        </div>
      </div>
    </section>

    <section class="hfl-detail-section" :class="{ 'policy-detail-overview__section--off': !policyForm.sectionRetentionEnabled }">
      <h4 class="hfl-detail-section__title">{{ t('protection.policiesPage.sectionRetention') }}</h4>
      <div class="hfl-detail-grid">
        <div v-if="retentionDetailLines.length" class="policy-detail-overview__retention-list">
          <div
            v-for="line in retentionDetailLines"
            :key="`${line.label}${line.text}`"
            class="policy-detail-overview__retention-line"
            :class="{ 'policy-detail-overview__retention-line--summary': !line.label }"
          >
            <span v-if="line.label" class="policy-detail-overview__retention-label">{{ line.label }}</span>
            <span class="policy-detail-overview__retention-text">{{ line.text }}</span>
          </div>
        </div>
        <div v-else class="hfl-detail-row hfl-detail-row--full">
          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldRetention') }}</span>
          <span class="hfl-detail-row__value">{{ notConfiguredText }}</span>
        </div>
      </div>
    </section>

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">{{ t('protection.policiesPage.sectionErrorHandling') }}</h4>
      <div class="policy-detail-overview__list">
        <div v-for="row in errorHandlingRows" :key="row.key" class="policy-detail-overview__list-row">
          <div class="policy-detail-overview__list-copy">
            <span class="policy-detail-overview__list-title">{{ row.title }}</span>
            <span class="policy-detail-overview__list-desc">{{ row.desc }}</span>
          </div>
          <ElTag :type="booleanStatusTag(policyForm[row.key]).type" :class="booleanStatusTag(policyForm[row.key]).class" size="small">{{ enabledText(policyForm[row.key]) }}</ElTag>
        </div>
      </div>
    </section>
  </div>
</template>

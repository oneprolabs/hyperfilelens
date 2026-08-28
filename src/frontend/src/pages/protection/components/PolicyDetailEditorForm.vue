<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatLocalDateTime } from '../../../lib/dateTime'
import {
  formatScheduleStartForDisplay,
  humanizeCronExpression,
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
const notConfiguredText = computed(() => t('protection.policiesPage.retentionNotConfigured'))

const quickScheduleLabel = computed(() => {
  const f = props.policyForm
  if (f.quickScheduleType === 'interval') {
    const unitKey = f.simpleIntervalUnit === 'minute'
      ? 'unitMinutes'
      : f.simpleIntervalUnit === 'hour' ? 'unitHours' : 'unitDays'
    return t('protection.policiesPage.previewScheduleInterval', {
      n: f.simpleIntervalValue,
      unit: Number(f.simpleIntervalValue) === 1
        ? t(`protection.policiesPage.${unitKey}`).replace(/s$/, '')
        : t(`protection.policiesPage.${unitKey}`),
    })
  }
  if (f.quickScheduleType === 'daily') return t('protection.policiesPage.previewScheduleDaily', { time: f.scheduleTime })
  if (f.quickScheduleType === 'weekly') {
    const weekdayKeys = ['weekdayMon', 'weekdayTue', 'weekdayWed', 'weekdayThu', 'weekdayFri', 'weekdaySat', 'weekdaySun']
    const weekdays = f.scheduleWeekdays.map((day) => t(`protection.policiesPage.${weekdayKeys[day - 1]}`)).join(', ')
    return t('protection.policiesPage.previewScheduleWeekly', { weekdays, time: f.scheduleTime })
  }
  const dates = f.scheduleMonthDays.map(String)
  if (f.scheduleMonthEnd) dates.push(t('protection.policiesPage.scheduleMonthEnd'))
  return t('protection.policiesPage.previewScheduleMonthly', { dates: dates.join(', '), time: f.scheduleTime })
})

const cronDescription = computed(() =>
  props.policyForm.sectionScheduleEnabled
    ? humanizeCronExpression(props.policyForm.cronExpr, messageLocale.value)
    : notConfiguredText.value,
)

const retentionDetailLines = computed(() => {
  const f = props.policyForm
  if (!f.sectionRetentionEnabled) return []
  const lines = [
    {
      label: '',
      text: t(f.retentionRecentPoints === 1 ? 'protection.policiesPage.retentionLatestOne' : 'protection.policiesPage.retentionLatestMany', { n: f.retentionRecentPoints }),
      summary: true,
    },
  ]
  if (f.retentionShortHourly) {
    lines.push({
      label: '',
      summary: false,
      text: t('protection.policiesPage.shortDesc', { days: f.retentionShortDaysMax }),
    })
  }
  if (f.retentionMidDaily) {
    lines.push({
      label: '',
      summary: false,
      text: t('protection.policiesPage.midDesc', { start: f.retentionShortDaysMax, end: f.retentionMidDaysMax }),
    })
  }
  if (f.retentionLongMonthly) {
    lines.push({
      label: '',
      summary: false,
      text: t('protection.policiesPage.longDesc', { day: f.retentionMidDaysMax, months: f.retentionLongMonths }),
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
    <section
      v-if="!hideInfoSection"
      class="hfl-detail-section"
    >
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.sectionPolicyInfo') }}
      </h4>
      <div class="hfl-detail-grid">
        <div class="policy-detail-editor__pair-row">
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldName') }}</span>
            <span class="hfl-detail-row__value policy-detail-overview__name-value">
              <span class="hfl-detail-row__text policy-detail-overview__primary">{{ policyForm.name || emptyText }}</span>
              <ElTag
                :type="booleanStatusTag(policyForm.policyActive).type"
                :class="booleanStatusTag(policyForm.policyActive).class"
                size="small"
              >{{ enabledText(policyForm.policyActive) }}</ElTag>
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

    <section
      class="hfl-detail-section"
      :class="{ 'policy-detail-overview__section--off': !policyForm.sectionScheduleEnabled }"
    >
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.sectionSchedule') }}
      </h4>
      <div class="hfl-detail-grid">
        <div
          v-if="!policyForm.sectionScheduleEnabled"
          class="hfl-detail-row hfl-detail-row--full"
        >
          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldSchedule') }}</span>
          <span class="hfl-detail-row__value">{{ notConfiguredText }}</span>
        </div>
        <div
          v-else
          class="hfl-detail-row hfl-detail-row--full"
        >
          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.scheduleCycle') }}</span>
          <span
            class="hfl-detail-row__value"
            :class="{ 'hfl-detail-row__value--stacked': policyForm.freqMode === 'advanced' }"
          >
            <code
              v-if="policyForm.freqMode === 'advanced'"
              class="policy-detail-overview__code"
            >{{ policyForm.cronExpr }}</code>
            <span
              v-else
              class="hfl-detail-row__text"
            >{{ quickScheduleLabel }}</span>
            <span
              v-if="policyForm.freqMode === 'advanced'"
              class="hfl-detail-row__hint"
            >{{ cronDescription }}</span>
          </span>
        </div>
        <div
          v-if="policyForm.sectionScheduleEnabled"
          class="policy-detail-editor__pair-row policy-detail-editor__pair-row--schedule-meta"
        >
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.scheduleTimezone') }}</span>
            <span class="hfl-detail-row__value">{{ policyForm.scheduleTimezone || 'UTC' }}</span>
          </div>
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.scheduleStartsAt') }}</span>
            <span
              class="hfl-detail-row__value"
              :class="{ 'hfl-detail-row__empty': !policyForm.scheduleStartsAt }"
            >{{ formatScheduleStartForDisplay(policyForm.scheduleStartsAt, emptyText) }}</span>
          </div>
        </div>
      </div>
    </section>

    <section
      class="hfl-detail-section"
      :class="{ 'policy-detail-overview__section--off': !policyForm.sectionRetentionEnabled }"
    >
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.sectionRetention') }}
      </h4>
      <div class="hfl-detail-grid">
        <div
          v-if="retentionDetailLines.length"
          class="policy-detail-overview__retention-list"
        >
          <div
            v-for="line in retentionDetailLines"
            :key="`${line.label}${line.text}`"
            class="policy-detail-overview__retention-line"
            :class="{
              'policy-detail-overview__retention-line--summary': line.summary,
              'policy-detail-overview__retention-line--full': !line.label && !line.summary,
            }"
          >
            <span
              v-if="line.label"
              class="policy-detail-overview__retention-label"
            >{{ line.label }}</span>
            <span class="policy-detail-overview__retention-text">{{ line.text }}</span>
          </div>
        </div>
        <div
          v-else
          class="hfl-detail-row hfl-detail-row--full"
        >
          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldRetention') }}</span>
          <span class="hfl-detail-row__value">{{ notConfiguredText }}</span>
        </div>
      </div>
    </section>

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.sectionErrorHandling') }}
      </h4>
      <div class="policy-detail-overview__list">
        <div
          v-for="row in errorHandlingRows"
          :key="row.key"
          class="policy-detail-overview__list-row"
        >
          <div class="policy-detail-overview__list-copy">
            <span class="policy-detail-overview__list-title">{{ row.title }}</span>
            <span class="policy-detail-overview__list-desc">{{ row.desc }}</span>
          </div>
          <ElTag
            :type="booleanStatusTag(policyForm[row.key]).type"
            :class="booleanStatusTag(policyForm[row.key]).class"
            size="small"
          >
            {{ enabledText(policyForm[row.key]) }}
          </ElTag>
        </div>
      </div>
    </section>
  </div>
</template>

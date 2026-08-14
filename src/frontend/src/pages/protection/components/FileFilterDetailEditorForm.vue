<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { formatLocalDateTime } from '../../../lib/dateTime'
import {
  compileFilterIgnorePatterns,
  type FileFilterRuleForm,
} from '../../../lib/protectionPolicyFormModel'
import { booleanStatusTag } from '../../../lib/statusTag'

const props = defineProps<{
  filterForm: FileFilterRuleForm
  createdAt?: string
  associatedSourceCount: number
  updatedAt?: string
  hideInfoSection?: boolean
}>()

const { t, locale } = useI18n()
const emptyText = computed(() => t('protection.policiesPage.timeDash'))
const statusOnText = computed(() => t('protection.policiesPage.switchEnabledOn'))
const statusOffText = computed(() => t('protection.policiesPage.switchEnabledOff'))
const noneText = computed(() => 'None')

const filterPresets = computed(() => [
  {
    key: 'presetTempFiles' as const,
    title: t('protection.policiesPage.filterPresetTempTitle'),
    sub: t('protection.policiesPage.filterPresetTempSub'),
  },
  {
    key: 'presetDevDeps' as const,
    title: t('protection.policiesPage.filterPresetDevTitle'),
    sub: t('protection.policiesPage.filterPresetDevSub'),
  },
  {
    key: 'presetSystemJunk' as const,
    title: t('protection.policiesPage.filterPresetSystemTitle'),
    sub: t('protection.policiesPage.filterPresetSystemSub'),
  },
])

const enabledFilterPresets = computed(() =>
  filterPresets.value.filter((preset) => props.filterForm[preset.key]),
)

const customExcludeRules = computed(() =>
  props.filterForm.customRules
    .map((rule) => rule.pattern.trim())
    .filter(Boolean),
)

const compiledRuleLines = computed(() =>
  compileFilterIgnorePatterns(props.filterForm)
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean),
)

const advancedRows = computed(() => [
  {
    key: 'large-file',
    title: t('protection.policiesPage.largeTitle'),
    desc: props.filterForm.largeFileLimitEnabled
      ? `${t('protection.policiesPage.largeLine')} ${props.filterForm.largeFileMax} ${props.filterForm.largeFileUnit}`
      : t('protection.policiesPage.previewNoLimit'),
    enabled: props.filterForm.largeFileLimitEnabled,
  },
  {
    key: 'cache',
    title: t('protection.policiesPage.cacheTitle'),
    desc: t('protection.policiesPage.cacheSub'),
    enabled: props.filterForm.ignoreCacheDirectories,
  },
  {
    key: 'fs-only',
    title: t('protection.policiesPage.fsOnlyTitle'),
    desc: t('protection.policiesPage.fsOnlySub'),
    enabled: props.filterForm.currentFilesystemOnly,
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
  <div class="hfl-detail-sections policy-detail-overview policy-detail-overview--filter filter-rule-form">
    <section
      v-if="!hideInfoSection"
      class="hfl-detail-section"
    >
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.sectionFileFilterInfo') }}
      </h4>
      <div class="hfl-detail-grid">
        <div class="policy-detail-editor__pair-row">
          <div class="hfl-detail-row policy-detail-editor__pair-item">
            <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldName') }}</span>
            <span class="hfl-detail-row__value policy-detail-overview__name-value">
              <span class="hfl-detail-row__text policy-detail-overview__primary">{{ filterForm.name || emptyText }}</span>
              <ElTag
                :type="booleanStatusTag(filterForm.policyActive).type"
                :class="booleanStatusTag(filterForm.policyActive).class"
                size="small"
              >{{ enabledText(filterForm.policyActive) }}</ElTag>
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

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.filterPresetSectionTitle') }}
      </h4>
      <div class="policy-detail-overview__list">
        <div
          v-for="preset in enabledFilterPresets"
          :key="preset.key"
          class="policy-detail-overview__list-row"
        >
          <div class="policy-detail-overview__list-copy">
            <span class="policy-detail-overview__list-title">{{ preset.title }}</span>
            <span class="policy-detail-overview__list-desc">{{ preset.sub }}</span>
          </div>
        </div>
        <div
          v-if="!enabledFilterPresets.length"
          class="policy-detail-overview__list-row"
        >
          <span class="policy-detail-overview__empty">{{ noneText }}</span>
        </div>
      </div>
    </section>

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.filterExcludeRulesTitle') }}
      </h4>
      <div class="policy-detail-overview__code-list">
        <code
          v-for="(rule, index) in customExcludeRules"
          :key="`${index}-${rule}`"
          class="policy-detail-overview__code"
        >
          {{ rule }}
        </code>
        <span
          v-if="!customExcludeRules.length"
          class="policy-detail-overview__empty"
        >{{ t('protection.policiesPage.filterNoExcludeRules') }}</span>
      </div>
    </section>

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.filterFinalPreviewTitle') }}
      </h4>
      <div class="filter-rules-preview policy-detail-overview__rules-preview">
        <template v-if="compiledRuleLines.length">
          <div class="filter-rules-preview__group">
            <div class="filter-rules-preview__divider">
              {{ t('protection.policiesPage.filterExcludeRulesTitle') }}
            </div>
            <code
              v-for="(line, index) in compiledRuleLines"
              :key="`${index}-${line}`"
              class="filter-rules-preview__line"
            >
              {{ line }}
            </code>
          </div>
        </template>
        <p
          v-else
          class="filter-rule-group__empty"
        >
          {{ t('protection.policiesPage.filterNoActiveRules') }}
        </p>
      </div>
    </section>

    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.sectionAdvancedSettings') }}
      </h4>
      <div class="policy-detail-overview__list">
        <div
          v-for="row in advancedRows"
          :key="row.key"
          class="policy-detail-overview__list-row"
        >
          <div class="policy-detail-overview__list-copy">
            <span class="policy-detail-overview__list-title">{{ row.title }}</span>
            <span class="policy-detail-overview__list-desc">{{ row.desc }}</span>
          </div>
          <ElTag
            :type="booleanStatusTag(row.enabled).type"
            :class="booleanStatusTag(row.enabled).class"
            size="small"
          >
            {{ enabledText(row.enabled) }}
          </ElTag>
        </div>
      </div>
    </section>
  </div>
</template>

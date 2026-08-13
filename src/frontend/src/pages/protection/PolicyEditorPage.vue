<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { ArrowLeft, Filter, ShieldCheck } from 'lucide-vue-next'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'
import ProtectionPolicyEditorForm from './components/ProtectionPolicyEditorForm.vue'
import { apiErrorMessage } from '../../lib/api'
import {
  createBackupPolicy,
  createFileFilterRule,
  getBackupPolicy,
  getFileFilterRule,
  updateBackupPolicy,
  updateFileFilterRule,
} from '../../lib/protectionPolicyApi'
import {
  backupPolicyToForm,
  buildFilterPresetsPreviewSummary,
  createEmptyFileFilterForm,
  createEmptyPolicyForm,
  fileFilterFormToWritePayload,
  fileFilterRuleToForm,
  policyFormToWritePayload,
  summarizeSchedule,
  validateRetentionForm,
  validateScheduleForm,
  type BackupPolicyForm,
  type FileFilterRuleForm,
  type MessageLocale,
} from '../../lib/protectionPolicyFormModel'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const form = ref<BackupPolicyForm>(createEmptyPolicyForm())
const filterForm = ref<FileFilterRuleForm>(createEmptyFileFilterForm())
const loading = ref(false)
const saving = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)

const editingId = computed(() => {
  const raw = route.params.id
  const value = Array.isArray(raw) ? raw[0] : raw
  const id = Number(value)
  return Number.isFinite(id) && id > 0 ? id : null
})
const editorKind = computed<'backup' | 'filter'>(() => route.query.kind === 'filter' ? 'filter' : 'backup')
const messageLocale = computed<MessageLocale>(() => 'en')
const isFilterEditor = computed(() => editorKind.value === 'filter')

async function loadEditorData() {
  if (!editingId.value) return
  loading.value = true
  try {
    if (isFilterEditor.value) {
      filterForm.value = fileFilterRuleToForm(await getFileFilterRule(editingId.value))
    } else {
      form.value = backupPolicyToForm(await getBackupPolicy(editingId.value))
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('protection.policiesPage.msgListLoadFailed')), grouping: true })
  } finally {
    loading.value = false
  }
}

function handleBack() {
  void router.push({
    path: '/protection/policies',
    query: { tab: isFilterEditor.value ? 'filter' : 'backup' },
  })
}

async function saveFilterRule() {
  const name = filterForm.value.name.trim()
  if (!validateInline([{ field: 'name', message: t('protection.policiesPage.msgNameRequired'), valid: !!name }])) return
  const snapshot = JSON.parse(JSON.stringify({ ...filterForm.value, name })) as FileFilterRuleForm
  saving.value = true
  try {
    if (editingId.value) {
      await updateFileFilterRule(editingId.value, fileFilterFormToWritePayload(snapshot))
      ElMessage.success({ message: t('protection.policiesPage.msgFilterUpdated'), grouping: true })
    } else {
      await createFileFilterRule(fileFilterFormToWritePayload(snapshot))
      ElMessage.success({ message: t('protection.policiesPage.msgFilterAdded'), grouping: true })
    }
    handleBack()
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('protection.policiesPage.msgSaveFailed')), grouping: true })
  } finally {
    saving.value = false
  }
}

async function savePolicy() {
  const name = form.value.name.trim()
  const scheduleError = validateScheduleForm(form.value)
  const showScheduleError = form.value.freqMode !== 'advanced' ? scheduleError : ''
  const retentionError = validateRetentionForm(form.value, messageLocale.value)
  const showRetentionError = retentionError === 'Latest restore points must be at least 1.' ? '' : retentionError
  if (!validateInline([
    { field: 'name', message: t('protection.policiesPage.msgPolicyNameRequired'), valid: !!name },
    { field: 'schedule', message: showScheduleError || '', valid: !scheduleError },
    { field: 'retention', message: showRetentionError || '', valid: !form.value.sectionRetentionEnabled || !retentionError },
  ])) return
  const snapshot = JSON.parse(JSON.stringify({ ...form.value, name })) as BackupPolicyForm
  saving.value = true
  try {
    if (editingId.value) {
      await updateBackupPolicy(editingId.value, policyFormToWritePayload(snapshot))
      ElMessage.success({ message: t('protection.policiesPage.msgPolicyUpdated'), grouping: true })
    } else {
      await createBackupPolicy(policyFormToWritePayload(snapshot))
      ElMessage.success({ message: t('protection.policiesPage.msgPolicyAdded'), grouping: true })
    }
    handleBack()
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('protection.policiesPage.msgSaveFailed')), grouping: true })
  } finally {
    saving.value = false
  }
}

function submitEditor() {
  if (saving.value) return
  if (isFilterEditor.value) void saveFilterRule()
  else void savePolicy()
}

const pageTitle = computed(() => {
  if (isFilterEditor.value) {
    return editingId.value
      ? t('protection.policiesPage.dialogEditFilterTitle')
      : t('protection.policiesPage.dialogAddFilterTitle')
  }
  return editingId.value
    ? t('protection.policiesPage.dialogEditTitle')
    : t('protection.policiesPage.dialogAddTitle')
})

const editorHeaderDesc = computed(() =>
  isFilterEditor.value
    ? t('protection.policiesPage.createTypeFilterSub')
    : t('protection.policiesPage.createTypeBackupSub'),
)

const editorSaveLabel = computed(() =>
  isFilterEditor.value
    ? t('protection.policiesPage.btnSaveFilter')
    : t('protection.policiesPage.btnSavePolicy'),
)

const editorPreviewName = computed(() =>
  isFilterEditor.value ? filterForm.value.name.trim() : form.value.name.trim(),
)
const editorPreviewType = computed(() =>
  isFilterEditor.value
    ? t('protection.policiesPage.createTypeFilter')
    : t('protection.policiesPage.createTypeBackup'),
)
const editorPreviewIcon = computed(() => isFilterEditor.value ? Filter : ShieldCheck)
const editorPreviewActive = computed(() =>
  isFilterEditor.value ? filterForm.value.policyActive : form.value.policyActive,
)
const previewNumber = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value : '—'

const backupRetentionPreviewRows = computed(() => {
  const f = form.value
  if (!f.sectionRetentionEnabled) {
    return [
      {
        label: t('protection.policiesPage.fieldRetention'),
        value: 'Not configured',
        muted: true,
      },
    ]
  }
  return [
    {
      label: 'Latest',
      value: `${previewNumber(f.retentionRecentPoints)} restore point(s)`,
    },
    {
      label: t('protection.policiesPage.shortTitle'),
      value: f.retentionShortHourly
          ? `First ${previewNumber(f.retentionShortDaysMax)} days`
        : t('protection.policiesPage.statusOff'),
      muted: !f.retentionShortHourly,
    },
    {
      label: t('protection.policiesPage.midTitle'),
      value: f.retentionMidDaily
          ? `Day ${previewNumber(f.retentionShortDaysMax)} to ${previewNumber(f.retentionMidDaysMax)}`
        : t('protection.policiesPage.statusOff'),
      muted: !f.retentionMidDaily,
    },
    {
      label: t('protection.policiesPage.longTitle'),
      value: f.retentionLongMonthly
          ? `After day ${previewNumber(f.retentionMidDaysMax)}, ${previewNumber(f.retentionLongMonths)} months`
        : t('protection.policiesPage.statusOff'),
      muted: !f.retentionLongMonthly,
    },
  ]
})

const backupErrorHandlingPreviewRows = computed(() => {
  const f = form.value
  const statusLabel = (enabled: boolean) => enabled
    ? t('protection.policiesPage.statusOn')
    : t('protection.policiesPage.statusOff')
  return [
    {
      label: t('protection.policiesPage.errRow1Title'),
      value: statusLabel(f.errorIgnoreDirectory),
      muted: !f.errorIgnoreDirectory,
    },
    {
      label: t('protection.policiesPage.errRow2Title'),
      value: statusLabel(f.errorIgnoreFile),
      muted: !f.errorIgnoreFile,
    },
    {
      label: t('protection.policiesPage.errRow3Title'),
      value: statusLabel(f.errorIgnoreUnknownEntries),
      muted: !f.errorIgnoreUnknownEntries,
    },
  ]
})

const editorPreviewRows = computed(() => {
  if (isFilterEditor.value) {
    return []
  }
  return [
    {
      label: t('protection.policiesPage.fieldSchedule'),
      value: summarizeSchedule(form.value, messageLocale.value),
    },
  ]
})

const filterRulePreviewRows = computed(() => {
  const f = filterForm.value
  const loc = messageLocale.value
  return [
    {
      label: t('protection.policiesPage.previewFilterPresets'),
      value: buildFilterPresetsPreviewSummary(f, loc),
    },
  ]
})

const filterAdvancedPreviewRows = computed(() => {
  const f = filterForm.value
  return [
    {
      label: t('protection.policiesPage.previewMaxSizeLimit'),
      value: f.largeFileLimitEnabled
        ? `>${f.largeFileMax} ${f.largeFileUnit}`
        : t('protection.policiesPage.previewNoLimit'),
      muted: !f.largeFileLimitEnabled,
    },
    {
      label: t('protection.policiesPage.cacheTitle'),
      value: f.ignoreCacheDirectories
        ? t('protection.policiesPage.statusOn')
        : t('protection.policiesPage.statusOff'),
      badge: true,
      active: f.ignoreCacheDirectories,
    },
    {
      label: t('protection.policiesPage.fsOnlyTitle'),
      value: f.currentFilesystemOnly
        ? t('protection.policiesPage.statusOn')
        : t('protection.policiesPage.statusOff'),
      badge: true,
      active: f.currentFilesystemOnly,
    },
  ]
})

onMounted(() => {
  void loadEditorData()
})
</script>

<template>
  <div ref="pageRef" class="fullscreen-form-fullscreen resource-add-fullscreen policy-editor-fullscreen">
    <div class="fullscreen-form-page add-s3-page">
      <header class="fullscreen-form-header">
        <button
          type="button"
          class="fullscreen-form-header__back"
          :aria-label="t('protection.policiesPage.policyEditorBackAria')"
          @click="handleBack"
        >
          <ArrowLeft class="fullscreen-form-header__back-icon" :size="18" />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">{{ pageTitle }}</h1>
          <p class="fullscreen-form-header__desc">{{ editorHeaderDesc }}</p>
        </div>
      </header>

      <div v-loading="loading" class="fullscreen-form-layout">
        <main class="fullscreen-form-main">
          <ProtectionPolicyEditorForm
            v-model:policy-form="form"
            v-model:filter-form="filterForm"
            :show-backup="!isFilterEditor"
            :show-filter="isFilterEditor"
            variant="fullscreen"
            cron-input-id="policy-cron-expr"
            :name-error="errors.name"
            :schedule-error="errors.schedule"
            :retention-error="errors.retention"
            @clear-name-error="clearFieldError('name')"
          />

          <footer class="fullscreen-form-footer fullscreen-form-action-footer">
            <ElButton :disabled="saving" @click="handleBack">
              {{ t('protection.backupsPage.btnCancel') }}
            </ElButton>
            <ElButton type="primary" :loading="saving" :disabled="saving || loading" @click="submitEditor">
              {{ editorSaveLabel }}
            </ElButton>
          </footer>
        </main>

        <aside class="fullscreen-form-sidebar add-form-preview-sidebar">
          <div class="add-form-preview-card">
            <div class="add-form-preview-header">
              <div class="add-form-preview-header__glow" />
              <div class="add-form-preview-header__icon">
                <component :is="editorPreviewIcon" class="add-form-preview-header__cloud" :size="28" />
              </div>
              <div class="add-form-preview-header__info">
                <h4
                  class="add-form-preview-header__name"
                  :class="{ 'add-form-preview-header__name--empty': !editorPreviewName }"
                >
                  {{ editorPreviewName || t('protection.policiesPage.previewUnnamed') }}
                </h4>
                <p class="add-form-preview-header__type">{{ editorPreviewType }}</p>
              </div>
            </div>

            <div class="add-form-preview-body">
              <div class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">{{ t('ops.alertsCenter.editor.previewTitle') }}</h5>
                <div class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ t('protection.policiesPage.fieldPolicyStatus') }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="editorPreviewActive ? 'add-form-preview-row__value--success' : 'add-form-preview-row__value--muted'"
                  >
                    {{ editorPreviewActive ? t('protection.policiesPage.statusOn') : t('protection.policiesPage.statusOff') }}
                  </span>
                </div>
                <div v-for="row in editorPreviewRows" :key="row.label" class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{
                      'add-form-preview-row__value--empty': !row.value,
                      'add-form-preview-row__value--success': row.badge && row.active,
                      'add-form-preview-row__value--muted': row.badge && !row.active,
                    }"
                  >
                    {{ row.value || t('protection.policiesPage.timeDash') }}
                  </span>
                </div>
              </div>

              <div v-if="isFilterEditor" class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">{{ t('protection.policiesPage.filterRulesSectionTitle') }}</h5>
                <div v-for="row in filterRulePreviewRows" :key="row.label" class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--muted': row.muted }"
                  >
                    {{ row.value }}
                  </span>
                </div>
              </div>

              <div v-if="isFilterEditor" class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">{{ t('protection.policiesPage.previewFilterAdvanced') }}</h5>
                <div v-for="row in filterAdvancedPreviewRows" :key="row.label" class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{
                      'add-form-preview-row__value--muted': row.muted,
                      'add-form-preview-row__value--success': row.badge && row.active,
                    }"
                  >
                    {{ row.value }}
                  </span>
                </div>
              </div>

              <div v-if="!isFilterEditor" class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">{{ t('protection.policiesPage.fieldRetention') }}</h5>
                <div v-for="row in backupRetentionPreviewRows" :key="row.label" class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="{ 'add-form-preview-row__value--muted': row.muted }"
                  >
                    {{ row.value }}
                  </span>
                </div>
              </div>

              <div v-if="!isFilterEditor" class="add-form-preview-section">
                <h5 class="add-form-preview-section__title">{{ t('protection.policiesPage.sectionCheckError') }}</h5>
                <div v-for="row in backupErrorHandlingPreviewRows" :key="row.label" class="add-form-preview-row">
                  <span class="add-form-preview-row__label">{{ row.label }}</span>
                  <span
                    class="add-form-preview-row__value"
                    :class="row.muted ? 'add-form-preview-row__value--muted' : 'add-form-preview-row__value--success'"
                  >
                    {{ row.value }}
                  </span>
                </div>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  </div>
</template>

<style src="../../styles/fullscreen-form-shell.css"></style>
<style src="../../styles/resource-add.css"></style>
<style src="../../styles/policy-editor-page.css"></style>
<style scoped>
.policy-editor-fullscreen .policy-section-nested {
  margin-top: 8px;
  padding: 18px;
  border: 1px solid rgba(226, 232, 240, 0.95);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.94) 0%, rgba(248, 250, 252, 0.92) 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.78);
}

.policy-editor-fullscreen .policy-cron-help {
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.96) 0%, rgba(241, 245, 249, 0.92) 100%);
  border-color: rgba(226, 232, 240, 0.95);
}

.policy-editor-fullscreen :deep(.add-form-preview-row) {
  align-items: flex-start;
}

.policy-editor-fullscreen :deep(.add-form-preview-row__label) {
  flex: 1 1 auto;
  min-width: 0;
  line-height: 1.35;
  overflow-wrap: anywhere;
}

.policy-editor-fullscreen :deep(.add-form-preview-row__value) {
  flex: 0 1 auto;
  max-width: 58%;
}

</style>

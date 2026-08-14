<script setup lang="ts">
import { ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, Pencil, X } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import HflBooleanStatusTag from '../../../components/HflBooleanStatusTag.vue'
import FileFilterDetailEditorForm from './FileFilterDetailEditorForm.vue'
import { apiErrorMessage } from '../../../lib/api'
import {
  getFileFilterRule,
  updateFileFilterRule,
} from '../../../lib/protectionPolicyApi'
import {
  createEmptyFileFilterForm,
  fileFilterRuleToForm,
  type FileFilterRuleForm,
} from '../../../lib/protectionPolicyFormModel'

const props = defineProps<{
  filterId: number
  createdAt: string
  associatedSourceCount: number
  updatedAt?: string
}>()

const emit = defineEmits<{
  updated: []
}>()

const { t } = useI18n()

const loading = ref(false)
const saving = ref(false)
const filterForm = ref<FileFilterRuleForm>(createEmptyFileFilterForm())
const editingField = ref<'name' | 'active' | null>(null)
const inlineDraft = ref<string | boolean>('')
let loadSeq = 0

async function loadFilter() {
  const seq = ++loadSeq
  loading.value = true
  try {
    const rule = await getFileFilterRule(props.filterId)
    if (seq !== loadSeq) return
    filterForm.value = fileFilterRuleToForm(rule)
    cancelInlineEdit()
  } catch (err) {
    if (seq !== loadSeq) return
    ElMessage.error({ message: apiErrorMessage(err, t('protection.policiesPage.msgListLoadFailed')), grouping: true })
  } finally {
    if (seq === loadSeq) loading.value = false
  }
}

function beginInlineEdit(field: 'name' | 'active') {
  if (saving.value || loading.value) return
  editingField.value = field
  inlineDraft.value = field === 'name' ? filterForm.value.name : filterForm.value.policyActive
}

function cancelInlineEdit() {
  editingField.value = null
  inlineDraft.value = ''
}

async function saveInlineEdit() {
  const field = editingField.value
  if (!field || saving.value) return
  const payload: { name?: string; is_active?: boolean } = {}
  if (field === 'name') {
    const name = String(inlineDraft.value).trim()
    if (!name) {
      ElMessage.warning({ message: t('protection.policiesPage.msgNameRequired'), grouping: true })
      return
    }
    payload.name = name
  } else {
    payload.is_active = Boolean(inlineDraft.value)
  }

  saving.value = true
  try {
    const updated = await updateFileFilterRule(props.filterId, payload)
    filterForm.value = fileFilterRuleToForm(updated)
    cancelInlineEdit()
    emit('updated')
    ElMessage.success({ message: t('protection.policiesPage.msgFilterUpdated'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err), grouping: true })
  } finally {
    saving.value = false
  }
}

watch(
  () => props.filterId,
  (id) => {
    if (id > 0) void loadFilter()
  },
  { immediate: true },
)
</script>

<template>
  <div
    v-loading="loading"
    class="policy-detail-basic-panel policy-detail-overview"
  >
    <section class="hfl-detail-section">
      <h4 class="hfl-detail-section__title">
        {{ t('protection.policiesPage.sectionFileFilterInfo') }}
      </h4>
      <div class="hfl-detail-grid">
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldName') }}</span>
          <span class="hfl-detail-row__value hfl-detail-row__value--editable">
            <template v-if="editingField === 'name'">
              <ElInput
                v-model="inlineDraft"
                size="small"
                class="hfl-detail-inline-edit__input"
                :disabled="saving"
                @keyup.enter="saveInlineEdit"
              />
              <span class="hfl-detail-inline-edit__actions">
                <ElButton
                  text
                  circle
                  size="small"
                  :title="t('common.save')"
                  :disabled="saving"
                  @click="saveInlineEdit"
                ><Check :size="14" /></ElButton>
                <ElButton
                  text
                  circle
                  size="small"
                  :title="t('common.cancel')"
                  :disabled="saving"
                  @click="cancelInlineEdit"
                ><X :size="14" /></ElButton>
              </span>
            </template>
            <template v-else>
              <span class="hfl-detail-row__text">{{ filterForm.name || t('protection.policiesPage.timeDash') }}</span>
              <ElButton
                text
                circle
                size="small"
                class="hfl-detail-row__edit"
                :title="t('common.edit')"
                @click="beginInlineEdit('name')"
              >
                <Pencil :size="13" />
              </ElButton>
            </template>
          </span>
        </div>
        <div class="hfl-detail-row">
          <span class="hfl-detail-row__label">{{ t('protection.policiesPage.fieldStatus') }}</span>
          <span class="hfl-detail-row__value hfl-detail-row__value--editable">
            <template v-if="editingField === 'active'">
              <ElSwitch
                v-model="inlineDraft"
                :disabled="saving"
              />
              <span class="hfl-detail-inline-edit__actions">
                <ElButton
                  text
                  circle
                  size="small"
                  :title="t('common.save')"
                  :disabled="saving"
                  @click="saveInlineEdit"
                ><Check :size="14" /></ElButton>
                <ElButton
                  text
                  circle
                  size="small"
                  :title="t('common.cancel')"
                  :disabled="saving"
                  @click="cancelInlineEdit"
                ><X :size="14" /></ElButton>
              </span>
            </template>
            <template v-else>
              <HflBooleanStatusTag
                :value="filterForm.policyActive"
                :label="filterForm.policyActive ? t('protection.policiesPage.switchEnabledOn') : t('protection.policiesPage.switchEnabledOff')"
              />
              <ElButton
                text
                circle
                size="small"
                class="hfl-detail-row__edit"
                :title="t('common.edit')"
                @click="beginInlineEdit('active')"
              >
                <Pencil :size="13" />
              </ElButton>
            </template>
          </span>
        </div>
      </div>
    </section>
    <FileFilterDetailEditorForm
      :filter-form="filterForm"
      :created-at="createdAt"
      :associated-source-count="associatedSourceCount"
      :updated-at="updatedAt"
      hide-info-section
    />
  </div>
</template>

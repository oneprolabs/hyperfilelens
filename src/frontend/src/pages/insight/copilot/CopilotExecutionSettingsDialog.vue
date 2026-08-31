<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import {
  patchCopilotSessionExecution,
  type LensAnalysisType,
  type LensSessionLink,
} from '../../../lib/lensApi'
import { apiErrorMessage } from '../../../lib/api'

const props = withDefaults(defineProps<{
  modelValue: boolean
  session: LensSessionLink | null
  supportedAnalysisTypes?: LensAnalysisType[]
}>(), {
  supportedAnalysisTypes: () => ['knowledge_qa', 'code_analysis'],
})

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'saved', value: LensSessionLink): void
}>()

const { t } = useI18n()
const saving = ref(false)
const analysisType = ref<LensAnalysisType>('knowledge_qa')
const analysisTypes: LensAnalysisType[] = ['knowledge_qa', 'code_analysis']

watch(() => [props.modelValue, props.session?.id], () => {
  if (!props.modelValue || !props.session) return
  analysisType.value = props.session.analysis_type
    || (props.session.selected_task === 'code_analysis' ? 'code_analysis' : 'knowledge_qa')
}, { immediate: true })

function analysisTypeLabel(type: LensAnalysisType): string {
  return type === 'code_analysis'
    ? t('insight.copilot.analysisTypeCodeAnalysis')
    : t('insight.copilot.analysisTypeKnowledgeQa')
}

async function save() {
  if (!props.session || saving.value) return
  saving.value = true
  try {
    const updated = await patchCopilotSessionExecution(props.session.id, {
      analysis_type: analysisType.value,
    })
    emit('saved', updated)
    emit('update:modelValue', false)
    ElMessage.success({ message: t('insight.copilot.executionSettingsSaved'), grouping: true })
  } catch (error) {
    ElMessage.error({
      message: apiErrorMessage(error, t('insight.copilot.executionSettingsFailed')),
      grouping: true,
    })
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <ElDialog
    :model-value="modelValue"
    :title="t('insight.copilot.executionSettingsTitle')"
    width="520px"
    append-to-body
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="copilot-execution-settings">
      <div class="fullscreen-form-field">
        <label class="fullscreen-form-field__label">{{ t('insight.copilot.analysisTypeLabel') }}</label>
        <ElSelect
          v-model="analysisType"
          class="copilot-execution-settings__select"
        >
          <ElOption
            v-for="type in analysisTypes"
            :key="type"
            :label="analysisTypeLabel(type)"
            :value="type"
            :disabled="!supportedAnalysisTypes.includes(type)"
          />
        </ElSelect>
        <p class="fullscreen-form-field__hint">
          {{ analysisType === 'code_analysis'
            ? t('insight.copilot.analysisTypeCodeAnalysisHint')
            : t('insight.copilot.analysisTypeKnowledgeQaHint') }}
        </p>
      </div>
      <p class="copilot-execution-settings__note">
        {{ t('insight.copilot.executionSettingsApplyHint') }}
      </p>
    </div>
    <template #footer>
      <ElButton @click="emit('update:modelValue', false)">
        {{ t('insight.copilot.btnCancel') }}
      </ElButton>
      <ElButton
        type="primary"
        :loading="saving"
        @click="save"
      >
        {{ t('insight.copilot.btnSave') }}
      </ElButton>
    </template>
  </ElDialog>
</template>

<style scoped>
.copilot-execution-settings { display: grid; gap: 20px; }
.copilot-execution-settings__select { width: 100%; }
.copilot-execution-settings__note { margin: 0; padding: 10px 12px; border: 1px solid #e5e6eb; border-radius: 8px; background: #f7f8fa; color: #86909c; font-size: 12px; line-height: 1.5; }
</style>

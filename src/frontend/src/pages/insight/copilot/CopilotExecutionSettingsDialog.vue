<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { patchCopilotSessionExecution, type LensAnalysisMode, type LensLlmConfig, type LensSessionLink } from '../../../lib/lensApi'
import { apiErrorMessage } from '../../../lib/api'

const props = defineProps<{
  modelValue: boolean
  session: LensSessionLink | null
  models: LensLlmConfig[]
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'saved', value: LensSessionLink): void
}>()

const { t } = useI18n()
const saving = ref(false)
const analysisMode = ref<LensAnalysisMode>('standard')
const modelRef = ref<string | null>(null)
const analysisModes: LensAnalysisMode[] = ['fast', 'standard', 'deep']

const availableModels = computed(() => props.models.filter((row) => (
  row.is_active !== false
  && !row.is_deployment_history
  && row.deployment_role !== 'multimodal'
  && row.uuid !== props.session?.multimodal_model_ref
)))

watch(() => [props.modelValue, props.session?.id], () => {
  if (!props.modelValue || !props.session) return
  analysisMode.value = props.session.analysis_mode || 'standard'
  modelRef.value = props.session.agent_model_ref || null
}, { immediate: true })

function modelLabel(row: LensLlmConfig): string {
  const name = String(row.name || '').trim()
  if (name) return name
  return `${row.provider || 'Model'} · ${row.config?.model || row.uuid}`
}

function modeLabel(mode: LensAnalysisMode): string {
  if (mode === 'fast') return 'Fast'
  if (mode === 'deep') return 'Deep'
  return 'Standard (recommended)'
}

async function save() {
  if (!props.session || saving.value) return
  saving.value = true
  try {
    const payload: {
      analysis_mode: LensAnalysisMode
      agent_model_ref?: string
    } = {
      analysis_mode: analysisMode.value,
    }
    if (
      modelRef.value
      && availableModels.value.some((model) => model.uuid === modelRef.value)
    ) {
      payload.agent_model_ref = modelRef.value
    }
    const updated = await patchCopilotSessionExecution(props.session.id, payload)
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
        <label class="fullscreen-form-field__label">{{ t('insight.copilot.analysisModeLabel') }}</label>
        <ElSelect
          v-model="analysisMode"
          class="copilot-execution-settings__select"
        >
          <ElOption
            v-for="mode in analysisModes"
            :key="mode"
            :label="modeLabel(mode)"
            :value="mode"
          />
        </ElSelect>
        <p class="fullscreen-form-field__hint">
          {{ t('insight.copilot.analysisModeHint') }}
        </p>
      </div>
      <div class="fullscreen-form-field">
        <label class="fullscreen-form-field__label">{{ t('insight.copilot.conversationModelLabel') }}</label>
        <ElSelect
          v-model="modelRef"
          class="copilot-execution-settings__select"
          :disabled="!availableModels.length"
        >
          <ElOption
            v-for="model in availableModels"
            :key="model.uuid"
            :label="modelLabel(model)"
            :value="model.uuid"
          />
        </ElSelect>
        <p class="fullscreen-form-field__hint">
          {{ t('insight.copilot.conversationModelHint') }}
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

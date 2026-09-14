<script setup lang="ts">
import { ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useI18n } from 'vue-i18n'
import { BookOpenText, Code2, Info } from 'lucide-vue-next'
import '../../../components/backupSourceFlowActionDialog.css'
import {
  patchCopilotSessionExecution,
  type LensAnalysisType,
  type LensSessionLink,
} from '../../../lib/lensApi'
import { apiErrorMessage } from '../../../lib/api'

const props = defineProps<{
  modelValue: boolean
  session: LensSessionLink | null
}>()

const emit = defineEmits<{
  (event: 'update:modelValue', value: boolean): void
  (event: 'saved', value: LensSessionLink): void
}>()

const { t } = useI18n()
const saving = ref(false)
const analysisType = ref<LensAnalysisType>('knowledge_qa')
const analysisTypeOptions = [
  {
    value: 'knowledge_qa',
    labelKey: 'insight.copilot.analysisTypeKnowledgeQa',
    hintKey: 'insight.copilot.analysisTypeKnowledgeQaHint',
    icon: BookOpenText,
  },
  {
    value: 'code_analysis',
    labelKey: 'insight.copilot.analysisTypeCodeAnalysis',
    hintKey: 'insight.copilot.analysisTypeCodeAnalysisHint',
    icon: Code2,
  },
] satisfies Array<{
  value: LensAnalysisType
  labelKey: string
  hintKey: string
  icon: typeof BookOpenText
}>

watch(() => [props.modelValue, props.session?.id], () => {
  if (!props.modelValue || !props.session) return
  analysisType.value = props.session.analysis_type
    || (props.session.selected_task === 'code_analysis' ? 'code_analysis' : 'knowledge_qa')
}, { immediate: true })

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
    class="hfl-flow-action-dialog hfl-flow-action-dialog--confirm copilot-execution-settings-dialog"
    :title="t('insight.copilot.executionSettingsTitle')"
    width="min(680px, calc(100vw - 32px))"
    align-center
    append-to-body
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="copilot-execution-settings">
      <fieldset class="copilot-execution-settings__fieldset">
        <legend class="copilot-execution-settings__legend">
          {{ t('insight.copilot.analysisTypeLabel') }}
        </legend>
        <div class="copilot-execution-settings__options">
          <label
            v-for="option in analysisTypeOptions"
            :key="option.value"
            class="copilot-execution-settings__option"
            :class="{ 'is-selected': analysisType === option.value }"
          >
            <input
              v-model="analysisType"
              class="copilot-execution-settings__input"
              type="radio"
              name="chat-analysis-type"
              :value="option.value"
            >
            <span class="copilot-execution-settings__option-icon">
              <component
                :is="option.icon"
                :size="18"
                aria-hidden="true"
              />
            </span>
            <span class="copilot-execution-settings__option-copy">
              <strong>{{ t(option.labelKey) }}</strong>
              <small>{{ t(option.hintKey) }}</small>
            </span>
            <span
              class="copilot-execution-settings__radio"
              aria-hidden="true"
            >
              <span />
            </span>
          </label>
        </div>
      </fieldset>
      <div class="copilot-execution-settings__note">
        <Info
          :size="16"
          aria-hidden="true"
        />
        <p>{{ t('insight.copilot.executionSettingsApplyHint') }}</p>
      </div>
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
.copilot-execution-settings { display: grid; gap: 16px; }
.copilot-execution-settings__fieldset { min-width: 0; margin: 0; padding: 0; border: 0; }
.copilot-execution-settings__legend { margin-bottom: 10px; color: var(--color-text-primary); font-size: 13px; font-weight: 600; }
.copilot-execution-settings__options { display: grid; gap: 10px; }
.copilot-execution-settings__option { position: relative; display: grid; grid-template-columns: 36px minmax(0, 1fr) 18px; gap: 12px; align-items: center; min-height: 88px; box-sizing: border-box; padding: 12px 14px; border: 1px solid var(--color-border); border-radius: 10px; background: var(--color-card-bg); cursor: pointer; transition: border-color .16s ease, background-color .16s ease, box-shadow .16s ease; }
.copilot-execution-settings__input { position: absolute; width: 1px; height: 1px; overflow: hidden; margin: -1px; padding: 0; border: 0; clip: rect(0 0 0 0); clip-path: inset(50%); white-space: nowrap; }
.copilot-execution-settings__option:hover { border-color: color-mix(in srgb, var(--color-primary) 48%, var(--color-border)); background: color-mix(in srgb, var(--color-primary) 3%, var(--color-card-bg)); }
.copilot-execution-settings__option:focus-within { outline: 2px solid color-mix(in srgb, var(--color-primary) 32%, transparent); outline-offset: 2px; }
.copilot-execution-settings__option.is-selected { border-color: var(--color-primary); background: color-mix(in srgb, var(--color-primary) 6%, var(--color-card-bg)); box-shadow: 0 0 0 1px color-mix(in srgb, var(--color-primary) 12%, transparent); }
.copilot-execution-settings__option-icon { display: inline-flex; width: 36px; height: 36px; align-items: center; justify-content: center; border-radius: 9px; background: color-mix(in srgb, var(--color-primary) 10%, var(--color-card-bg)); color: var(--color-primary); }
.copilot-execution-settings__option-copy { display: grid; min-width: 0; gap: 4px; }
.copilot-execution-settings__option-copy strong { color: var(--color-text-title); font-size: 14px; font-weight: 600; line-height: 1.4; }
.copilot-execution-settings__option-copy small { color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.copilot-execution-settings__radio { display: inline-flex; width: 16px; height: 16px; align-items: center; justify-content: center; border: 1.5px solid var(--color-text-tertiary); border-radius: 50%; background: var(--color-card-bg); transition: border-color .16s ease, box-shadow .16s ease; }
.copilot-execution-settings__radio span { width: 8px; height: 8px; border-radius: 50%; background: transparent; }
.copilot-execution-settings__option.is-selected .copilot-execution-settings__radio { border-color: var(--color-primary); box-shadow: 0 0 0 2px color-mix(in srgb, var(--color-primary) 10%, transparent); }
.copilot-execution-settings__option.is-selected .copilot-execution-settings__radio span { background: var(--color-primary); }
.copilot-execution-settings__note { display: flex; gap: 8px; align-items: flex-start; margin: 0; padding: 10px 12px; border-radius: 8px; background: color-mix(in srgb, var(--color-primary) 4%, var(--color-card-bg)); color: var(--color-text-secondary); font-size: 12px; line-height: 1.5; }
.copilot-execution-settings__note svg { flex: 0 0 auto; margin-top: 1px; color: var(--color-primary); }
.copilot-execution-settings__note p { margin: 0; }

@media (max-width: 480px) {
  .copilot-execution-settings__option { grid-template-columns: 32px minmax(0, 1fr) 18px; gap: 10px; padding: 12px; }
  .copilot-execution-settings__option-icon { width: 32px; height: 32px; }
}
</style>

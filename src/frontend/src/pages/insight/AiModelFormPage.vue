<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensModelsPath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, ChevronDown, CircleCheck, CircleX, SlidersHorizontal } from 'lucide-vue-next'
import AiProviderIcon from '../../components/ai-model/AiProviderIcon.vue'
import {
  aiModelReferencePriceLine,
  useAiModelForm,
  type AiModelAdvancedParameter,
} from '../../composables/useAiModelForm'

const { t, locale } = useI18n()
const route = useRoute()
const router = useRouter()

const editingUuid = computed(() => {
  const raw = route.params.uuid
  return typeof raw === 'string' && raw ? raw : null
})

const isEditing = computed(() => Boolean(editingUuid.value))

const {
  loading,
  saving,
  testing,
  testOk,
  testDetail,
  testSummary,
  apiKeyRequiredForSave,
  modelDropdownOpen,
  useCustomModel,
  providers,
  form,
  currentProviderModels,
  advancedParameters,
  apiBaseRequired,
  selectedModelInfo,
  hasSelectedModelInfo,
  modelSelectLabel,
  capabilityLabel,
  capabilityClass,
  selectModel,
  onNameInput,
  init,
  runTest,
  submit,
  modelCapabilities,
  updateAdvancedParameter,
} = useAiModelForm(editingUuid)

const pageTitle = computed(() =>
  isEditing.value ? t('insight.aiSettings.editModel') : t('insight.aiSettings.addModelPageTitle'),
)

const pageDesc = computed(() =>
  isEditing.value ? t('insight.aiSettings.editModelPageDesc') : t('insight.aiSettings.addModelPageDesc'),
)

const busy = computed(() => loading.value || saving.value || testing.value)
const intlLocale = computed(() => locale.value === 'en' ? 'en-US' : locale.value)
const tuningParameters = computed(() => (
  advancedParameters.value.filter((parameter) => parameter.inputType !== 'boolean')
))

function formatTokenCount(value: number) {
  return new Intl.NumberFormat(intlLocale.value).format(value)
}

function providerModelCount(count: number) {
  return t('insight.aiSettings.providerModelCount', { n: count }, count)
}

function referencePriceLine(
  pricing: Parameters<typeof aiModelReferencePriceLine>[0],
) {
  return aiModelReferencePriceLine(
    pricing,
    {
      input: t('insight.aiSettings.referencePriceInput'),
      output: t('insight.aiSettings.referencePriceOutput'),
    },
    intlLocale.value,
  )
}

const testDuration = computed(() => {
  const durationMs = testSummary.value?.durationMs
  if (durationMs == null) return ''
  if (durationMs < 1000) return `${durationMs} ms`
  return `${(durationMs / 1000).toFixed(durationMs < 10_000 ? 1 : 0)} s`
})

const submitLabel = computed(() => {
  if (isEditing.value) return t('common.save')
  if (!saving.value) return t('insight.aiSettings.btnCreateModel')
  return form.is_active
    ? t('insight.aiSettings.testingAndAddingModel')
    : t('insight.aiSettings.addingModel')
})

function handleBack() {
  router.push(lensModelsPath())
}

function advancedParameterPlaceholder(parameter: AiModelAdvancedParameter) {
  if (parameter.defaultValue !== null && parameter.defaultValue !== undefined) {
    return t('insight.aiSettings.providerDefaultValue', { value: parameter.defaultValue })
  }
  return t('insight.aiSettings.optionalProviderValue')
}

function onVisionCapabilityChange(event: Event) {
  updateAdvancedParameter('vision', (event.target as HTMLInputElement).checked)
}

async function handleSubmit() {
  const ok = await submit()
  if (ok) router.push(lensModelsPath())
}

onMounted(async () => {
  await init()
  if (isEditing.value && route.query.enable === '1') {
    form.is_active = true
  }
})
</script>

<template>
  <div class="fullscreen-form-fullscreen resource-add-fullscreen ai-model-form-fullscreen">
    <div class="fullscreen-form-page">
      <header class="fullscreen-form-header">
        <button
          type="button"
          class="fullscreen-form-header__back"
          @click="handleBack"
        >
          <ArrowLeft
            class="fullscreen-form-header__back-icon"
            :size="18"
          />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            {{ pageTitle }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ pageDesc }}
          </p>
        </div>
      </header>

      <div
        v-loading="loading"
        class="fullscreen-form-layout"
      >
        <div class="fullscreen-form-main">
          <div class="fullscreen-form-step-stack">
            <!-- Provider -->
            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.aiSettings.sectionProvider') }}
              </h3>
              <p class="ai-model-section-desc">
                {{ t('insight.aiSettings.sectionProviderDesc') }}
              </p>

              <ElRadioGroup
                v-model="form.provider"
                class="ai-provider-grid"
                :disabled="isEditing"
              >
                <ElRadio
                  v-for="provider in providers"
                  :key="provider.id"
                  :value="provider.id"
                  border
                  class="ai-provider-card !mr-0"
                >
                  <div class="ai-provider-card__inner">
                    <AiProviderIcon
                      :provider="provider.id"
                      size="lg"
                    />
                    <div class="ai-provider-card__text">
                      <div class="ai-provider-card__name">
                        {{ provider.label || provider.name || provider.id }}
                      </div>
                      <div class="ai-provider-card__meta">
                        {{ providerModelCount(provider.models?.length ?? 0) }}
                      </div>
                    </div>
                  </div>
                </ElRadio>
              </ElRadioGroup>
            </section>

            <!-- Connection -->
            <section
              v-if="form.provider"
              class="fullscreen-form-card fullscreen-form-section ai-model-section--connection"
            >
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.aiSettings.sectionCredentials') }}
              </h3>

              <ElForm
                label-position="top"
                class="fullscreen-form-el-form"
              >
                <ElFormItem
                  :label="t('insight.aiSettings.labelModel')"
                  required
                >
                  <div class="ai-model-dropdown">
                    <button
                      type="button"
                      class="ai-model-dropdown__trigger"
                      @click="modelDropdownOpen = !modelDropdownOpen"
                    >
                      <span class="truncate">{{ modelSelectLabel }}</span>
                      <ChevronDown
                        :size="16"
                        class="shrink-0 text-[var(--color-text-tertiary)]"
                      />
                    </button>
                    <div
                      v-show="modelDropdownOpen"
                      class="ai-model-dropdown__panel"
                    >
                      <button
                        v-for="model in currentProviderModels"
                        :key="model.id"
                        type="button"
                        class="ai-model-dropdown__item"
                        :class="{ 'is-active': !useCustomModel && form.model === model.id }"
                        @click="selectModel(model.id)"
                      >
                        <div class="ai-model-dropdown__item-title">
                          {{ model.label || model.name || model.id }}
                        </div>
                        <div
                          v-if="modelCapabilities(model).length"
                          class="ai-model-dropdown__caps"
                        >
                          <span
                            v-for="cap in modelCapabilities(model)"
                            :key="cap"
                            class="ai-cap-tag"
                            :class="capabilityClass(cap)"
                          >
                            {{ capabilityLabel(cap) }}
                          </span>
                        </div>
                      </button>
                      <div class="ai-model-dropdown__divider" />
                      <button
                        type="button"
                        class="ai-model-dropdown__item ai-model-dropdown__item--custom"
                        :class="{ 'is-active': useCustomModel }"
                        @click="selectModel('__custom__')"
                      >
                        {{ t('insight.aiSettings.modelCustom') }}
                      </button>
                    </div>
                  </div>
                  <ElInput
                    v-if="useCustomModel"
                    v-model="form.model"
                    class="mt-2"
                    :placeholder="t('insight.aiSettings.modelPlaceholder')"
                  />
                </ElFormItem>

                <div
                  v-if="hasSelectedModelInfo && selectedModelInfo"
                  class="ai-model-info-card"
                >
                  <div
                    v-if="selectedModelInfo.capabilities.length"
                    class="ai-model-info-card__section"
                  >
                    <div class="ai-model-info-card__label">
                      {{ t('insight.aiSettings.labelCapabilities') }}
                    </div>
                    <div class="ai-model-dropdown__caps">
                      <span
                        v-for="cap in selectedModelInfo.capabilities"
                        :key="cap"
                        class="ai-cap-tag"
                        :class="capabilityClass(cap)"
                      >
                        {{ capabilityLabel(cap) }}
                      </span>
                    </div>
                  </div>
                  <dl
                    v-if="selectedModelInfo.max_input_tokens || selectedModelInfo.max_output_tokens"
                    class="ai-model-info-card__metrics"
                  >
                    <div v-if="selectedModelInfo.max_input_tokens">
                      <dt>{{ t('insight.aiSettings.maxInputTokens') }}</dt>
                      <dd>{{ formatTokenCount(selectedModelInfo.max_input_tokens) }}</dd>
                    </div>
                    <div v-if="selectedModelInfo.max_output_tokens">
                      <dt>{{ t('insight.aiSettings.maxOutputTokens') }}</dt>
                      <dd>{{ formatTokenCount(selectedModelInfo.max_output_tokens) }}</dd>
                    </div>
                  </dl>
                  <div
                    v-if="referencePriceLine(selectedModelInfo.reference_pricing)"
                    class="ai-model-info-card__price"
                  >
                    <span>{{ t('insight.aiSettings.referencePrice') }}</span>
                    {{ referencePriceLine(selectedModelInfo.reference_pricing) }}
                  </div>
                </div>

                <div
                  v-if="form.provider === 'openai_compatible'"
                  class="ai-model-capability-card"
                >
                  <div class="ai-model-capability-card__title">
                    {{ t('insight.aiSettings.modelCapabilities') }}
                  </div>
                  <p class="ai-model-capability-card__desc">
                    {{ t('insight.aiSettings.modelCapabilitiesHint') }}
                  </p>
                  <label class="ai-model-capability-card__option">
                    <input
                      type="checkbox"
                      :checked="form.advanced.vision === true"
                      @change="onVisionCapabilityChange"
                    >
                    <span>
                      <span class="ai-model-capability-card__option-title">
                        {{ t('insight.aiSettings.confirmVisionSupport') }}
                      </span>
                      <span class="ai-model-capability-card__option-desc">
                        {{ t('insight.aiSettings.confirmVisionSupportHint') }}
                      </span>
                    </span>
                  </label>
                </div>

                <ElFormItem
                  :label="t('insight.aiSettings.labelApiKey')"
                  :required="apiKeyRequiredForSave"
                >
                  <ElInput
                    v-model="form.api_key"
                    type="password"
                    show-password
                    :placeholder="apiKeyRequiredForSave
                      ? t('insight.aiSettings.apiKeyPlaceholder')
                      : t('insight.aiSettings.apiKeyKeepPlaceholder')"
                  />
                  <p class="ai-model-field-hint">
                    {{ t('insight.aiSettings.apiKeyEncryptHint') }}
                  </p>
                </ElFormItem>

                <ElFormItem
                  :label="t('insight.aiSettings.labelApiBase')"
                  :required="apiBaseRequired"
                >
                  <ElInput
                    v-model="form.api_base"
                    :placeholder="t('insight.aiSettings.apiBasePlaceholder')"
                  />
                  <p class="ai-model-field-hint">
                    {{ apiBaseRequired
                      ? t('insight.aiSettings.apiBaseRequiredHint')
                      : t('insight.aiSettings.apiBaseHint') }}
                  </p>
                </ElFormItem>

                <ElFormItem :label="t('insight.aiSettings.colName')">
                  <ElInput
                    v-model="form.name"
                    :placeholder="t('insight.aiSettings.namePlaceholder')"
                    @input="onNameInput"
                  />
                  <p class="ai-model-field-hint">
                    {{ t('insight.aiSettings.nameHint') }}
                  </p>
                </ElFormItem>

                <details
                  v-if="tuningParameters.length"
                  class="ai-model-advanced"
                  :open="tuningParameters.some((parameter) => parameter.required)"
                >
                  <summary class="ai-model-advanced__summary">
                    <span class="ai-model-advanced__summary-main">
                      <span class="ai-model-advanced__icon">
                        <SlidersHorizontal
                          :size="16"
                          aria-hidden="true"
                        />
                      </span>
                      <span>
                        <span class="ai-model-advanced__title">
                          {{ t('insight.aiSettings.advancedOptions') }}
                        </span>
                        <span class="ai-model-advanced__desc">
                          {{ t('insight.aiSettings.advancedOptionsDesc') }}
                        </span>
                      </span>
                    </span>
                    <ChevronDown
                      :size="16"
                      class="ai-model-advanced__chevron"
                      aria-hidden="true"
                    />
                  </summary>
                  <div class="ai-model-advanced__fields">
                    <ElFormItem
                      v-for="parameter in tuningParameters"
                      :key="parameter.name"
                      :label="parameter.label"
                      :required="parameter.required"
                    >
                      <ElInput
                        v-if="parameter.inputType === 'number'"
                        :model-value="form.advanced[parameter.name] == null
                          ? ''
                          : String(form.advanced[parameter.name])"
                        type="number"
                        :min="parameter.min"
                        :max="parameter.max"
                        :step="parameter.step"
                        :placeholder="advancedParameterPlaceholder(parameter)"
                        @update:model-value="updateAdvancedParameter(parameter.name, $event)"
                      />
                      <ElInput
                        v-else
                        :model-value="String(form.advanced[parameter.name] ?? '')"
                        :placeholder="advancedParameterPlaceholder(parameter)"
                        @update:model-value="updateAdvancedParameter(parameter.name, $event)"
                      />
                    </ElFormItem>
                  </div>
                </details>

                <ElFormItem :label="t('insight.aiSettings.labelActive')">
                  <ElSwitch v-model="form.is_active" />
                  <p class="ai-model-field-hint">
                    {{ t('insight.aiSettings.activeHint') }}
                  </p>
                </ElFormItem>
              </ElForm>

              <div
                v-if="testOk != null"
                class="ai-model-test-result"
                :class="{ 'is-ok': testOk, 'is-fail': !testOk }"
                :role="testOk ? 'status' : 'alert'"
                :aria-live="testOk ? 'polite' : 'assertive'"
              >
                <div class="ai-model-test-result__header">
                  <CircleCheck
                    v-if="testOk"
                    :size="20"
                    aria-hidden="true"
                  />
                  <CircleX
                    v-else
                    :size="20"
                    aria-hidden="true"
                  />
                  <div>
                    <div class="ai-model-test-result__title">
                      {{ testOk
                        ? t('insight.aiSettings.connectionTestSuccessTitle')
                        : t('insight.aiSettings.connectionTestFailureTitle') }}
                    </div>
                    <p class="ai-model-test-result__detail">
                      {{ testDetail }}
                    </p>
                  </div>
                </div>

                <dl
                  v-if="testSummary"
                  class="ai-model-test-result__summary"
                >
                  <div>
                    <dt>{{ t('insight.aiSettings.connectionTestProvider') }}</dt>
                    <dd>{{ testSummary.provider }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('insight.aiSettings.connectionTestModel') }}</dt>
                    <dd>{{ testSummary.model }}</dd>
                  </div>
                  <div>
                    <dt>{{ t('insight.aiSettings.connectionTestEndpoint') }}</dt>
                    <dd>{{ testSummary.endpoint }}</dd>
                  </div>
                </dl>

                <p
                  v-if="testOk && testDuration"
                  class="ai-model-test-result__footer"
                >
                  {{ t('insight.aiSettings.connectionTestReady', { duration: testDuration }) }}
                </p>
              </div>
            </section>
          </div>
        </div>
      </div>

      <footer class="fullscreen-form-footer">
        <ElButton
          :loading="testing"
          :disabled="busy"
          @click="runTest"
        >
          {{ t('insight.aiSettings.testConnection') }}
        </ElButton>
        <ElButton
          :disabled="saving"
          @click="handleBack"
        >
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          type="primary"
          :loading="saving"
          :disabled="busy"
          @click="handleSubmit"
        >
          {{ submitLabel }}
        </ElButton>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.ai-model-form-fullscreen .fullscreen-form-page {
  min-height: calc(var(--app-viewport-height) - var(--app-header-height));
}

.ai-model-form-fullscreen .fullscreen-form-card {
  overflow: visible;
}

.ai-model-form-fullscreen .fullscreen-form-step-stack {
  overflow: visible;
}

.ai-model-section-desc {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-secondary);
}

.ai-provider-grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(min(220px, 100%), 1fr));
  gap: 12px;
  width: 100%;
}

.ai-provider-grid :deep(.el-radio) {
  height: auto !important;
  min-height: 64px;
  margin-right: 0 !important;
  padding: 12px 14px !important;
  border-radius: 12px !important;
  align-items: flex-start;
}

.ai-provider-grid :deep(.el-radio.is-bordered.is-checked) {
  border-color: var(--color-primary, #6d5ef6) !important;
  background: var(--color-primary-light, #f2f0fe);
}

.ai-provider-card__inner {
  display: flex;
  align-items: center;
  gap: 12px;
  width: 100%;
}

.ai-provider-card__text {
  flex: 1;
  min-width: 0;
}

.ai-provider-card__name {
  overflow: hidden;
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-title);
  text-overflow: ellipsis;
  white-space: nowrap;
}

.ai-provider-card__meta {
  margin-top: 2px;
  font-size: 11px;
  color: var(--color-text-tertiary);
}

.ai-model-dropdown {
  position: relative;
  width: 100%;
}

.ai-model-dropdown__trigger {
  display: flex;
  width: 100%;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control, 8px);
  background: var(--color-card-bg, #fff);
  padding: 8px 12px;
  font-size: 14px;
  color: var(--color-text-primary);
  cursor: pointer;
}

.ai-model-dropdown__panel {
  position: absolute;
  z-index: 40;
  top: calc(100% + 4px);
  left: 0;
  right: 0;
  max-height: min(320px, 50vh);
  overflow: auto;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-control, 8px);
  background: var(--color-card-bg, #fff);
  box-shadow: 0 12px 32px -20px rgba(15, 23, 42, 0.35);
}

.ai-model-section--connection :deep(.el-form-item) {
  overflow: visible;
}

.ai-model-dropdown__item {
  display: block;
  width: 100%;
  border: none;
  background: transparent;
  padding: 10px 12px;
  text-align: left;
  cursor: pointer;
}

.ai-model-dropdown__item:hover,
.ai-model-dropdown__item.is-active {
  background: var(--color-primary-light, #f2f0fe);
}

.ai-model-dropdown__item-title {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-title);
}

.ai-model-dropdown__caps {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
}

.ai-model-dropdown__divider {
  height: 1px;
  background: var(--color-border-light);
}

.ai-model-dropdown__item--custom {
  font-weight: 600;
  color: var(--color-text-secondary);
}

.ai-cap-tag {
  display: inline-flex;
  border-radius: 4px;
  padding: 2px 6px;
  font-size: 11px;
  font-weight: 600;
}

.cap-sky { background: #e0f2fe; color: #0369a1; }
.cap-emerald { background: #d1fae5; color: #047857; }
.cap-violet { background: #ede9fe; color: #6d28d9; }
.cap-indigo { background: #e0e7ff; color: #4338ca; }
.cap-rose { background: #ffe4e6; color: #be123c; }
.cap-teal { background: #ccfbf1; color: #0f766e; }
.cap-gray { background: #f1f5f9; color: #475569; }

.ai-model-info-card {
  margin-top: 4px;
  padding: 12px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-card, 12px);
  background: var(--color-grey-2, #f8fafc);
}

.ai-model-info-card__label {
  margin-bottom: 8px;
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.ai-model-info-card__section + .ai-model-info-card__metrics,
.ai-model-info-card__section + .ai-model-info-card__price,
.ai-model-info-card__metrics + .ai-model-info-card__price {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-light);
}

.ai-model-info-card__metrics {
  display: flex;
  flex-wrap: wrap;
  gap: 12px 28px;
  margin-bottom: 0;
}

.ai-model-info-card__metrics > div {
  display: flex;
  align-items: baseline;
  gap: 6px;
}

.ai-model-info-card__metrics dt,
.ai-model-info-card__price span {
  font-size: 12px;
  font-weight: 600;
  color: var(--color-text-secondary);
}

.ai-model-info-card__metrics dd,
.ai-model-info-card__price {
  margin: 0;
  font-size: 12px;
  color: var(--color-text-primary);
}

.ai-model-info-card__price span {
  margin-right: 6px;
}

.ai-model-capability-card {
  margin: 4px 0 18px;
  padding: 14px;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-card, 12px);
  background: var(--color-grey-2, #f8fafc);
}

.ai-model-capability-card__title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-title);
}

.ai-model-capability-card__desc {
  margin: 4px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-secondary);
}

.ai-model-capability-card__option {
  display: flex;
  min-height: 48px;
  align-items: flex-start;
  gap: 10px;
  margin-top: 10px;
  padding: 10px;
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
  cursor: pointer;
}

.ai-model-capability-card__option:hover {
  background: var(--color-primary-light, #f2f0fe);
}

.ai-model-capability-card__option:focus-within {
  outline: 2px solid var(--color-primary, #6d5ef6);
  outline-offset: -2px;
}

.ai-model-capability-card__option input {
  width: 16px;
  height: 16px;
  flex: none;
  margin: 2px 0 0;
  accent-color: var(--color-primary, #6d5ef6);
}

.ai-model-capability-card__option-title,
.ai-model-capability-card__option-desc {
  display: block;
}

.ai-model-capability-card__option-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--color-text-primary);
}

.ai-model-capability-card__option-desc {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.ai-model-advanced {
  margin-bottom: 18px;
  overflow: hidden;
  border: 1px solid var(--color-border-light);
  border-radius: var(--radius-card, 12px);
  background: var(--color-grey-2, #f8fafc);
}

.ai-model-advanced__summary {
  display: flex;
  min-height: 52px;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 14px;
  color: var(--color-text-title);
  cursor: pointer;
  list-style: none;
}

.ai-model-advanced__summary::-webkit-details-marker {
  display: none;
}

.ai-model-advanced__summary:focus-visible {
  outline: 2px solid var(--color-primary, #6d5ef6);
  outline-offset: -2px;
}

.ai-model-advanced__summary-main {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
}

.ai-model-advanced__icon {
  display: inline-flex;
  width: 30px;
  height: 30px;
  flex: none;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
  color: var(--color-text-secondary);
}

.ai-model-advanced__title,
.ai-model-advanced__desc {
  display: block;
}

.ai-model-advanced__title {
  font-size: 13px;
  font-weight: 600;
}

.ai-model-advanced__desc {
  margin-top: 2px;
  font-size: 12px;
  line-height: 1.4;
  color: var(--color-text-secondary);
}

.ai-model-advanced__chevron {
  flex: none;
  color: var(--color-text-tertiary);
  transition: transform 180ms ease;
}

.ai-model-advanced[open] .ai-model-advanced__chevron {
  transform: rotate(180deg);
}

.ai-model-advanced__fields {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 2px 14px;
  padding: 14px;
  border-top: 1px solid var(--color-border-light);
  background: var(--color-card-bg, #fff);
}

.ai-model-advanced__fields :deep(.el-form-item) {
  margin-bottom: 14px;
}

@media (max-width: 720px) {
  .ai-model-advanced__fields {
    grid-template-columns: 1fr;
  }
}

.ai-model-field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.ai-model-test-result {
  margin-top: 8px;
  padding: 16px;
  border: 1px solid;
  border-radius: var(--radius-card, 12px);
}

.ai-model-test-result.is-ok {
  background: var(--color-success-light);
  color: var(--color-success-text);
  border-color: var(--color-success-border);
}

.ai-model-test-result.is-fail {
  background: var(--color-error-light);
  color: var(--color-error-text);
  border-color: var(--color-error-border);
}

.ai-model-test-result__header {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.ai-model-test-result__header > svg {
  flex: none;
  margin-top: 1px;
}

.ai-model-test-result__title {
  font-size: 14px;
  font-weight: 600;
  line-height: 1.4;
}

.ai-model-test-result__detail {
  margin: 3px 0 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.5;
}

.ai-model-test-result__summary {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 14px 0 0;
  padding-top: 14px;
  border-top: 1px solid color-mix(in srgb, currentColor 18%, transparent);
}

.ai-model-test-result__summary > div {
  min-width: 0;
}

.ai-model-test-result__summary dt {
  color: var(--color-text-tertiary);
  font-size: 12px;
  font-weight: 600;
  line-height: 1.4;
}

.ai-model-test-result__summary dd {
  overflow-wrap: anywhere;
  margin: 3px 0 0;
  color: var(--color-text-primary);
  font-size: 12px;
  line-height: 1.45;
}

.ai-model-test-result__footer {
  margin: 12px 0 0;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.4;
}

@media (max-width: 720px) {
  .ai-model-test-result__summary {
    grid-template-columns: 1fr;
    gap: 10px;
  }
}
</style>

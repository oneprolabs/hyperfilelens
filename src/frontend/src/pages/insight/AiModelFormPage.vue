<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensModelsPath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, ChevronDown, CircleCheck, CircleX } from 'lucide-vue-next'
import AiProviderIcon from '../../components/ai-model/AiProviderIcon.vue'
import { useAiModelForm } from '../../composables/useAiModelForm'

const { t } = useI18n()
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
  selectedModelInfo,
  modelSelectLabel,
  capabilityLabel,
  capabilityClass,
  selectModel,
  onNameInput,
  init,
  runTest,
  submit,
  modelCapabilities,
} = useAiModelForm(editingUuid)

const pageTitle = computed(() =>
  isEditing.value ? t('insight.aiSettings.editModel') : t('insight.aiSettings.addModelPageTitle'),
)

const pageDesc = computed(() =>
  isEditing.value ? t('insight.aiSettings.editModelPageDesc') : t('insight.aiSettings.addModelPageDesc'),
)

const busy = computed(() => loading.value || saving.value || testing.value)

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
                        {{ t('insight.aiSettings.providerModelCount', { n: provider.models?.length ?? 0 }) }}
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
                  v-if="selectedModelInfo"
                  class="ai-model-info-card"
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

                <ElFormItem :label="t('insight.aiSettings.labelApiBase')">
                  <ElInput
                    v-model="form.api_base"
                    :placeholder="t('insight.aiSettings.apiBasePlaceholder')"
                  />
                  <p class="ai-model-field-hint">
                    {{ t('insight.aiSettings.apiBaseHint') }}
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
  grid-template-columns: repeat(auto-fill, minmax(200px, 1fr));
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
  min-width: 0;
}

.ai-provider-card__name {
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-title);
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

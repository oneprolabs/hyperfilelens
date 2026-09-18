<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { lensSkillsPath } from '../../lib/lensEngineRoutes'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Sparkles } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../lib/api'
import { useInlineFormValidation } from '../../composables/useInlineFormValidation'
import { routeLocationWithListRefresh } from '../../lib/listRouteRefresh'
import {
  beautifyLensSkill,
  createLensSkill,
  fetchLensSkill,
  updateLensSkill,
  type LensSkill,
} from '../../lib/lensApi'
import { skillContent, skillDescription } from '../../lib/lensSkillHelpers'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const editingUuid = computed(() => {
  const raw = route.params.uuid
  return typeof raw === 'string' && raw ? raw : null
})

const isEditing = computed(() => Boolean(editingUuid.value))

const loading = ref(false)
const saving = ref(false)
const beautifying = ref(false)
const pageRef = ref<HTMLElement | null>(null)
const { clear: clearFieldError, errors, validate: validateInline } = useInlineFormValidation(pageRef)

const name = ref('')
const description = ref('')
const content = ref('')
const enabled = ref(true)

const pageTitle = computed(() => {
  if (isEditing.value && name.value.trim()) return name.value.trim()
  return t('insight.skills.addPageTitle')
})

const pageDesc = computed(() => t('insight.skills.addPageDesc'))

function applyRow(row: LensSkill) {
  name.value = row.name || ''
  description.value = skillDescription(row)
  content.value = skillContent(row)
  enabled.value = row.enabled !== false
}

function resetForm() {
  name.value = ''
  description.value = ''
  content.value = ''
  enabled.value = true
}

function buildPayload() {
  const definition: Record<string, string> = { content: content.value.trim() }
  const desc = description.value.trim()
  if (desc) definition.description = desc
  return {
    name: name.value.trim(),
    definition,
    enabled: enabled.value,
  }
}

async function loadDetail() {
  if (!editingUuid.value) return
  const row = await fetchLensSkill(editingUuid.value)
  applyRow(row)
}

async function init() {
  loading.value = true
  try {
    if (isEditing.value) {
      await loadDetail()
    } else {
      resetForm()
    }
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('errors.generic.loadFailed')))
  } finally {
    loading.value = false
  }
}

function handleBack() {
  router.push(routeLocationWithListRefresh(lensSkillsPath()))
}

async function handleBeautify() {
  beautifying.value = true
  try {
    const result = await beautifyLensSkill({
      name: name.value.trim(),
      content: content.value,
    })
    if (result.content) {
      content.value = result.content
      ElMessage.success(t('insight.skills.beautifySuccess'))
    }
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('insight.skills.beautifyFailed')))
  } finally {
    beautifying.value = false
  }
}

async function handleSubmit() {
  if (saving.value) return
  if (!validateInline([
    { field: 'name', message: t('insight.skills.fieldName'), valid: !!name.value.trim() },
    { field: 'content', message: t('insight.skills.fieldContent'), valid: !!content.value.trim() },
  ])) return
  saving.value = true
  try {
    const payload = buildPayload()
    if (isEditing.value && editingUuid.value) {
      await updateLensSkill(editingUuid.value, payload)
      ElMessage.success(t('insight.skills.saveSuccess'))
    } else {
      await createLensSkill(payload)
      ElMessage.success(t('insight.skills.createSuccess'))
    }
    router.push(routeLocationWithListRefresh(lensSkillsPath()))
  } catch (err) {
    ElMessage.error(apiErrorMessage(err, t('insight.skills.saveFailed')))
  } finally {
    saving.value = false
  }
}

watch(
  () => [route.path, route.params.uuid] as const,
  () => {
    void init()
  },
  { immediate: true },
)
</script>

<template>
  <div
    ref="pageRef"
    class="fullscreen-form-fullscreen resource-add-fullscreen skill-form-fullscreen"
  >
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
            <section class="fullscreen-form-card fullscreen-form-section">
              <h3 class="fullscreen-form-section__title">
                <span class="fullscreen-form-section__indicator" />
                {{ t('insight.skills.sectionBasics') }}
              </h3>
              <p class="skill-section-desc">
                {{ t('insight.skills.sectionBasicsDesc') }}
              </p>

              <ElForm
                label-position="top"
                class="fullscreen-form-el-form fullscreen-form-el-form--strong-label"
              >
                <ElFormItem
                  data-validation-field="name"
                  class="fullscreen-form-item--in-card"
                  :error="errors.name"
                  :label="t('insight.skills.fieldName')"
                  required
                >
                  <ElInput
                    v-model="name"
                    :placeholder="t('insight.skills.fieldNamePh')"
                    @input="clearFieldError('name')"
                  />
                  <p class="skill-field-hint">
                    {{ t('insight.skills.fieldNameHint') }}
                  </p>
                </ElFormItem>

                <ElFormItem
                  class="fullscreen-form-item--in-card"
                  :label="t('insight.skills.fieldDescription')"
                >
                  <ElInput
                    v-model="description"
                    :placeholder="t('insight.skills.fieldDescriptionPh')"
                  />
                  <p class="skill-field-hint">
                    {{ t('insight.skills.fieldDescriptionHint') }}
                  </p>
                </ElFormItem>

                <ElFormItem
                  class="fullscreen-form-item--in-card fullscreen-form-status-item"
                  :label="t('insight.skills.fieldEnabled')"
                >
                  <div class="skill-enabled-row">
                    <ElSwitch v-model="enabled" />
                    <span class="skill-field-hint skill-field-hint--inline">
                      {{ t('insight.skills.fieldEnabledHint') }}
                    </span>
                  </div>
                </ElFormItem>
              </ElForm>
            </section>

            <section class="fullscreen-form-card fullscreen-form-section">
              <div class="skill-content-header">
                <div class="skill-content-header__copy">
                  <h3 class="fullscreen-form-section__title">
                    <span class="fullscreen-form-section__indicator" />
                    {{ t('insight.skills.sectionContent') }}
                  </h3>
                  <p class="skill-section-desc skill-section-desc--tight">
                    {{ t('insight.skills.beautifyHint') }}
                  </p>
                </div>
                <ElButton
                  size="small"
                  :loading="beautifying"
                  :disabled="beautifying || loading"
                  @click="handleBeautify"
                >
                  <Sparkles :size="14" />
                  {{ t('insight.skills.beautify') }}
                </ElButton>
              </div>

              <ElForm
                label-position="top"
                class="fullscreen-form-el-form fullscreen-form-el-form--strong-label"
              >
                <ElFormItem
                  data-validation-field="content"
                  class="fullscreen-form-item--in-card"
                  :error="errors.content"
                  :label="t('insight.skills.fieldContent')"
                  required
                >
                  <ElInput
                    v-model="content"
                    type="textarea"
                    :rows="12"
                    :placeholder="t('insight.skills.fieldContentPh')"
                    class="skill-content-textarea"
                    @input="clearFieldError('content')"
                  />
                  <p class="skill-field-hint">
                    {{ t('insight.skills.fieldContentHint') }}
                  </p>
                </ElFormItem>
              </ElForm>
            </section>
          </div>
        </div>
      </div>

      <footer class="fullscreen-form-footer">
        <ElButton
          :disabled="saving"
          @click="handleBack"
        >
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton
          type="primary"
          :loading="saving"
          :disabled="saving || loading"
          @click="handleSubmit"
        >
          {{ isEditing ? t('common.save') : t('insight.skills.btnCreate') }}
        </ElButton>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.skill-form-fullscreen .fullscreen-form-page {
  min-height: calc(var(--app-viewport-height) - var(--app-header-height));
}

.skill-form-fullscreen .fullscreen-form-card,
.skill-form-fullscreen .fullscreen-form-step-stack {
  overflow: visible;
}

.skill-section-desc {
  margin: 0 0 14px;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-secondary);
}

.skill-section-desc--tight {
  margin-bottom: 0;
}

.skill-field-hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.45;
  color: var(--color-text-secondary);
}

.skill-field-hint--inline {
  margin: 0;
}

.skill-enabled-row {
  display: flex;
  align-items: center;
  gap: 12px;
  min-width: 0;
}

.skill-content-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 14px;
}

.skill-content-header__copy {
  min-width: 0;
  flex: 1;
}

.skill-content-header .fullscreen-form-section__title {
  margin-bottom: 6px;
}

.skill-content-textarea :deep(textarea) {
  min-height: 240px;
  max-height: 420px;
  resize: vertical;
  overflow-y: auto;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  line-height: 1.5;
}

@media (max-width: 720px) {
  .skill-content-header {
    flex-direction: column;
    align-items: stretch;
  }
}
</style>

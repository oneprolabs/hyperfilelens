<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Link2, Network } from 'lucide-vue-next'
import ModulePage from '../../../../components/ModulePage.vue'
import { clearDeployProfileCache, fetchDeployProfile } from '../../../../composables/useDeployProfile'
import { apiErrorMessage } from '../../../../lib/api'
import PlatformOpsDetailSection from '../../../components/PlatformOpsDetailSection.vue'
import { useResolvedPlatformOpsSideNav } from '../../../composables/useResolvedPlatformOpsSideNav'
import {
  fetchPlatformExternalAccess,
  patchPlatformExternalAccess,
  type PlatformExternalAccessSettings,
} from '../../../lib/platformOpsApi'

const { t } = useI18n()
const sideNav = useResolvedPlatformOpsSideNav()

const busy = ref(false)
const saving = ref(false)
const validationError = ref('')
const meta = ref<PlatformExternalAccessSettings | null>(null)
const externalAccessUrl = ref('')
const hasOverride = computed(() => meta.value?.source === 'runtime')

function applyPayload(payload: PlatformExternalAccessSettings) {
  meta.value = payload
  externalAccessUrl.value = payload.external_access_url || ''
}

async function load() {
  busy.value = true
  try {
    applyPayload(await fetchPlatformExternalAccess())
  } catch (err) {
    ElMessage.error({
      message: apiErrorMessage(err, t('platformOps.settings.loadFailed')),
      grouping: true,
    })
  } finally {
    busy.value = false
  }
}

async function update(value: string) {
  if (!meta.value?.editable) return
  saving.value = true
  validationError.value = ''
  try {
    applyPayload(await patchPlatformExternalAccess(value.trim()))
    clearDeployProfileCache()
    await fetchDeployProfile(true)
    ElMessage.success({
      message: t('platformOps.settings.externalAccess.saved'),
      grouping: true,
    })
  } catch (err) {
    validationError.value = apiErrorMessage(err, t('platformOps.settings.saveFailed'))
    ElMessage.error({
      message: validationError.value,
      grouping: true,
    })
  } finally {
    saving.value = false
  }
}

function useSuggestion() {
  if (!meta.value?.suggested_url) return
  externalAccessUrl.value = meta.value.suggested_url
  validationError.value = ''
}

onMounted(load)
</script>

<template>
  <ModulePage
    :menus="sideNav"
    body-fill
  >
    <div
      v-loading="busy"
      class="external-access"
    >
      <div
        v-if="meta"
        class="external-access__layout"
      >
        <PlatformOpsDetailSection
          :title="t('platformOps.settings.externalAccess.configurationTitle')"
          class="external-access__configuration"
        >
          <div class="external-access__section-body">
            <p class="external-access__intro">
              {{ t('platformOps.settings.externalAccess.intro') }}
            </p>

            <el-alert
              type="warning"
              show-icon
              :closable="false"
              :title="t('platformOps.settings.externalAccess.networkNotice')"
              class="external-access__notice"
            />

            <el-alert
              v-if="!meta.editable"
              type="info"
              show-icon
              :closable="false"
              :title="t('platformOps.settings.externalAccess.managed')"
              class="external-access__managed"
            />

            <el-form
              label-position="top"
              class="external-access__form"
              @submit.prevent="update(externalAccessUrl)"
            >
              <el-form-item
                :label="t('platformOps.settings.externalAccess.urlLabel')"
                :error="validationError"
              >
                <el-input
                  v-model="externalAccessUrl"
                  type="url"
                  autocomplete="url"
                  maxlength="2048"
                  :disabled="!meta.editable || saving"
                  :placeholder="t('platformOps.settings.externalAccess.urlPlaceholder')"
                  @input="validationError = ''"
                />
                <div class="external-access__hint">
                  {{ t('platformOps.settings.externalAccess.urlHint') }}
                </div>
              </el-form-item>

              <div
                v-if="meta.suggested_url && meta.editable"
                class="external-access__suggestion"
              >
                <span
                  class="external-access__suggestion-icon"
                  aria-hidden="true"
                >
                  <Link2 :size="16" />
                </span>
                <span class="external-access__suggestion-copy">
                  <span>{{ t('platformOps.settings.externalAccess.suggested') }}</span>
                  <code>{{ meta.suggested_url }}</code>
                </span>
                <el-button
                  link
                  type="primary"
                  :disabled="saving"
                  @click="useSuggestion"
                >
                  {{ t('platformOps.settings.externalAccess.useSuggested') }}
                </el-button>
              </div>

              <div
                v-if="meta.editable"
                class="external-access__actions"
              >
                <el-button
                  type="primary"
                  native-type="submit"
                  :loading="saving"
                >
                  {{ t('platformOps.settings.saveChanges') }}
                </el-button>
                <el-button
                  :disabled="saving || !hasOverride"
                  @click="update('')"
                >
                  {{ t('platformOps.settings.externalAccess.restoreAutomatic') }}
                </el-button>
              </div>
            </el-form>
          </div>
        </PlatformOpsDetailSection>

        <aside class="external-access__aside">
          <PlatformOpsDetailSection :title="t('platformOps.settings.externalAccess.effectiveTitle')">
            <div class="external-access__summary">
              <div class="external-access__summary-item">
                <span class="external-access__summary-label">
                  {{ t('platformOps.settings.externalAccess.effectiveUrl') }}
                </span>
                <code class="external-access__effective-url">{{ meta.effective_url }}</code>
              </div>
              <div class="external-access__summary-item external-access__summary-item--inline">
                <span class="external-access__summary-label">
                  {{ t('platformOps.settings.externalAccess.source') }}
                </span>
                <el-tag
                  type="info"
                  effect="plain"
                >
                  {{ t(`platformOps.settings.externalAccess.sourceValue.${meta.source}`) }}
                </el-tag>
              </div>
            </div>
          </PlatformOpsDetailSection>

          <PlatformOpsDetailSection :title="t('platformOps.settings.externalAccess.impactTitle')">
            <div class="external-access__impact">
              <span
                class="external-access__impact-icon"
                aria-hidden="true"
              >
                <Network :size="18" />
              </span>
              <p>{{ t('platformOps.settings.externalAccess.impact') }}</p>
            </div>
          </PlatformOpsDetailSection>
        </aside>
      </div>
    </div>
  </ModulePage>
</template>

<style scoped>
.external-access {
  width: 100%;
  min-height: 100%;
  overflow-y: auto;
}

.external-access__layout {
  display: grid;
  grid-template-columns: minmax(0, 1.8fr) minmax(300px, 0.9fr);
  gap: 16px;
  align-items: start;
  width: min(100%, 1160px);
  margin: 0 auto;
  padding-bottom: 24px;
}

.external-access__section-body {
  padding: 20px;
}

.external-access__intro {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  line-height: 1.65;
}

.external-access__form {
  margin-top: 24px;
}

.external-access__form :deep(.el-form-item__label) {
  color: var(--el-text-color-primary);
  font-weight: 600;
}

.external-access__notice {
  margin-top: 16px;
}

.external-access__managed {
  margin-top: 12px;
}

.external-access__notice :deep(.el-alert__content),
.external-access__managed :deep(.el-alert__content) {
  min-width: 0;
}

.external-access__hint {
  margin-top: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.external-access__suggestion {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr) auto;
  gap: 10px;
  align-items: center;
  margin: -2px 0 20px;
  padding: 11px 12px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);
  color: var(--color-text-secondary);
  font-size: 13px;
}

.external-access__suggestion-icon,
.external-access__impact-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  flex: 0 0 auto;
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
  color: var(--el-color-primary);
}

.external-access__suggestion-copy {
  display: grid;
  gap: 2px;
  min-width: 0;
}

.external-access__suggestion-copy code {
  overflow-wrap: anywhere;
  color: var(--color-text-primary);
  font-size: 12px;
}

.external-access__actions {
  display: flex;
  gap: 8px;
  padding-top: 18px;
  border-top: 1px solid var(--el-border-color-lighter);
}

.external-access__aside {
  display: grid;
  gap: 16px;
  min-width: 0;
}

.external-access__summary {
  display: grid;
}

.external-access__summary-item {
  display: grid;
  gap: 8px;
  min-width: 0;
  padding: 16px;
}

.external-access__summary-item + .external-access__summary-item {
  border-top: 1px solid var(--el-border-color-lighter);
}

.external-access__summary-item--inline {
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
}

.external-access__summary-label {
  color: var(--el-text-color-secondary);
  font-size: 12px;
  font-weight: 600;
}

.external-access__effective-url {
  display: block;
  min-width: 0;
  padding: 9px 10px;
  overflow-wrap: anywhere;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 7px;
  background: var(--el-fill-color-extra-light);
  color: var(--el-text-color-primary);
  font-size: 12px;
  line-height: 1.5;
}

.external-access__impact {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  padding: 16px;
}

.external-access__impact p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.65;
}

.external-access__impact-icon {
  width: 36px;
  height: 36px;
}

@media (max-width: 960px) {
  .external-access__layout {
    grid-template-columns: minmax(0, 1fr);
  }
}

@media (max-width: 640px) {
  .external-access__section-body {
    padding: 16px;
  }

  .external-access__suggestion {
    grid-template-columns: auto minmax(0, 1fr);
  }

  .external-access__suggestion :deep(.el-button) {
    grid-column: 2;
    justify-self: start;
    margin-left: 0;
  }

  .external-access__actions {
    flex-direction: column;
  }

  .external-access__actions :deep(.el-button) {
    width: 100%;
    margin-left: 0;
  }
}
</style>

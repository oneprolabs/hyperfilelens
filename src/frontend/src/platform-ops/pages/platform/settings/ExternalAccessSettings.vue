<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
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
        class="hfl-detail-sections"
      >
        <PlatformOpsDetailSection :title="t('platformOps.settings.externalAccessTitle')">
          <p class="external-access__intro">
            {{ t('platformOps.settings.externalAccess.intro') }}
          </p>

          <el-alert
            type="warning"
            :closable="false"
            :title="t('platformOps.settings.externalAccess.networkNotice')"
            class="external-access__notice"
          />

          <el-alert
            v-if="!meta.editable"
            type="info"
            :closable="false"
            :title="t('platformOps.settings.externalAccess.managed')"
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
              <span>{{ t('platformOps.settings.externalAccess.suggested') }}</span>
              <code>{{ meta.suggested_url }}</code>
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
        </PlatformOpsDetailSection>

        <PlatformOpsDetailSection :title="t('platformOps.settings.externalAccess.effectiveTitle')">
          <div class="hfl-detail-grid">
            <div class="hfl-detail-row hfl-detail-row--full">
              <span class="hfl-detail-row__label">{{ t('platformOps.settings.externalAccess.effectiveUrl') }}</span>
              <code class="hfl-detail-row__value hfl-detail-row__value--break">{{ meta.effective_url }}</code>
            </div>
            <div class="hfl-detail-row hfl-detail-row--full">
              <span class="hfl-detail-row__label">{{ t('platformOps.settings.externalAccess.source') }}</span>
              <span class="hfl-detail-row__value">
                {{ t(`platformOps.settings.externalAccess.sourceValue.${meta.source}`) }}
              </span>
            </div>
          </div>
        </PlatformOpsDetailSection>

        <PlatformOpsDetailSection :title="t('platformOps.settings.externalAccess.impactTitle')">
          <p class="external-access__impact">
            {{ t('platformOps.settings.externalAccess.impact') }}
          </p>
        </PlatformOpsDetailSection>
      </div>
    </div>
  </ModulePage>
</template>

<style scoped>
.external-access__intro,
.external-access__impact {
  margin: 0;
  color: var(--color-text-secondary);
  line-height: 1.6;
}

.external-access__form {
  max-width: 760px;
  margin-top: 20px;
}

.external-access__notice {
  margin-top: 16px;
}

.external-access__hint {
  margin-top: 6px;
  color: var(--color-text-secondary);
  font-size: 12px;
  line-height: 1.5;
}

.external-access__suggestion {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: center;
  margin: -4px 0 20px;
  color: var(--color-text-secondary);
  font-size: 13px;
}

.external-access__suggestion code {
  overflow-wrap: anywhere;
  color: var(--color-text-primary);
}

.external-access__actions {
  display: flex;
  gap: 8px;
}

@media (max-width: 640px) {
  .external-access__actions {
    flex-direction: column;
  }

  .external-access__actions :deep(.el-button) {
    width: 100%;
    margin-left: 0;
  }
}
</style>

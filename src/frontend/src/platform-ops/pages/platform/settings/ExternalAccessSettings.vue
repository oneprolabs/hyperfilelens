<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import ModulePage from '../../../../components/ModulePage.vue'
import { clearDeployProfileCache, fetchDeployProfile } from '../../../../composables/useDeployProfile'
import { apiErrorMessage } from '../../../../lib/api'
import PlatformOpsDetailSection from '../../../components/PlatformOpsDetailSection.vue'
import PlatformOpsRefreshButton from '../../../components/PlatformOpsRefreshButton.vue'
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
      <div class="external-access__toolbar">
        <PlatformOpsRefreshButton
          :loading="busy"
          :disabled="saving"
          @click="load"
        />
      </div>

      <div
        v-if="meta"
        class="hfl-detail-sections"
      >
        <PlatformOpsDetailSection :title="t('platformOps.settings.externalAccess.configurationTitle')">
          <div class="external-access__lead">
            <p>{{ t('platformOps.settings.externalAccess.intro') }}</p>
            <el-alert
              type="warning"
              show-icon
              :closable="false"
              :title="t('platformOps.settings.externalAccess.networkNotice')"
            />
            <el-alert
              v-if="!meta.editable"
              type="info"
              show-icon
              :closable="false"
              :title="t('platformOps.settings.externalAccess.managed')"
            />
          </div>

          <el-form @submit.prevent="update(externalAccessUrl)">
            <table class="external-access__table">
              <tbody>
                <tr>
                  <th scope="row">{{ t('platformOps.settings.externalAccess.urlLabel') }}</th>
                  <td>
                    <div class="external-access__field">
                      <el-input
                        v-model="externalAccessUrl"
                        type="url"
                        autocomplete="url"
                        maxlength="2048"
                        :disabled="!meta.editable || saving"
                        :placeholder="t('platformOps.settings.externalAccess.urlPlaceholder')"
                        @input="validationError = ''"
                      />
                      <p class="external-access__hint">
                        {{ t('platformOps.settings.externalAccess.urlHint') }}
                      </p>
                      <p
                        v-if="validationError"
                        class="external-access__error"
                      >
                        {{ validationError }}
                      </p>
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
                    </div>
                  </td>
                </tr>
                <tr v-if="meta.editable">
                  <th scope="row" />
                  <td>
                    <div class="external-access__actions">
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
                  </td>
                </tr>
              </tbody>
            </table>
          </el-form>
        </PlatformOpsDetailSection>

        <PlatformOpsDetailSection :title="t('platformOps.settings.externalAccess.effectiveTitle')">
          <table class="external-access__table">
            <tbody>
              <tr>
                <th scope="row">{{ t('platformOps.settings.externalAccess.effectiveUrl') }}</th>
                <td>
                  <span
                    class="external-access__mono"
                    :class="{ 'hfl-empty-mark': !meta.effective_url }"
                  >{{ meta.effective_url || '—' }}</span>
                </td>
              </tr>
              <tr>
                <th scope="row">{{ t('platformOps.settings.externalAccess.source') }}</th>
                <td>
                  <el-tag
                    size="small"
                    type="info"
                    effect="plain"
                  >
                    {{ t(`platformOps.settings.externalAccess.sourceValue.${meta.source}`) }}
                  </el-tag>
                </td>
              </tr>
            </tbody>
          </table>
        </PlatformOpsDetailSection>

        <PlatformOpsDetailSection :title="t('platformOps.settings.externalAccess.impactTitle')">
          <div class="external-access__lead external-access__lead--last">
            <p>{{ t('platformOps.settings.externalAccess.impact') }}</p>
          </div>
        </PlatformOpsDetailSection>
      </div>
    </div>
  </ModulePage>
</template>

<style scoped>
.external-access {
  --ea-label-width: 168px;
  width: 100%;
  min-height: 100%;
  overflow-y: auto;
}

.external-access__toolbar {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 12px;
}

.external-access__lead {
  display: grid;
  gap: 12px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.external-access__lead--last {
  border-bottom: 0;
}

.external-access__lead p {
  margin: 0;
  color: var(--color-text-secondary);
  font-size: 13px;
  line-height: 1.55;
}

.external-access__lead :deep(.el-alert__content) {
  min-width: 0;
}

.external-access__table {
  width: 100%;
  border-collapse: collapse;
  table-layout: fixed;
}

.external-access__table th,
.external-access__table td {
  padding: 12px 16px;
  vertical-align: top;
  text-align: left;
  font-size: 13px;
  line-height: 1.45;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.external-access__table tr:last-child th,
.external-access__table tr:last-child td {
  border-bottom: 0;
}

.external-access__table th {
  width: var(--ea-label-width);
  background: var(--el-fill-color-lighter);
  color: var(--el-text-color-secondary);
  font-weight: 400;
  border-right: 1px solid var(--el-border-color-lighter);
}

.external-access__table td {
  color: var(--el-text-color-primary);
}

.external-access__field {
  display: grid;
  gap: 8px;
  max-width: 640px;
}

.external-access__hint,
.external-access__error {
  margin: 0;
  font-size: 12px;
  line-height: 1.5;
}

.external-access__hint {
  color: var(--color-text-secondary);
}

.external-access__error {
  color: var(--el-color-danger);
}

.external-access__suggestion {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  padding: 8px 10px;
  border: 1px solid var(--el-border-color-lighter);
  border-radius: 8px;
  background: var(--el-fill-color-extra-light);
  color: var(--color-text-secondary);
  font-size: 12px;
}

.external-access__suggestion code {
  min-width: 0;
  overflow-wrap: anywhere;
  color: var(--color-text-primary);
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 12px;
}

.external-access__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.external-access__mono {
  display: inline-block;
  max-width: 100%;
  overflow-wrap: anywhere;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
}

@media (max-width: 720px) {
  .external-access__table,
  .external-access__table tbody,
  .external-access__table tr,
  .external-access__table th,
  .external-access__table td {
    display: block;
    width: 100%;
  }

  .external-access__table th {
    border-right: 0;
    border-bottom: 0;
    padding-bottom: 6px;
  }

  .external-access__table td {
    padding-top: 0;
  }

  .external-access__actions :deep(.el-button) {
    width: 100%;
    margin-left: 0;
  }
}
</style>

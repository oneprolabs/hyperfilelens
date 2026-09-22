<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import ModulePage from '../../../../components/ModulePage.vue'
import { clearDeployProfileCache, fetchDeployProfile } from '../../../../composables/useDeployProfile'
import { apiErrorMessage } from '../../../../lib/api'
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
const hasOverride = computed(
  () => meta.value?.has_runtime_override === true || meta.value?.source === 'runtime',
)

function applyPayload(payload: PlatformExternalAccessSettings) {
  meta.value = payload
  // Show the URL currently in effect (Runtime override, else Deployment/Default).
  externalAccessUrl.value = payload.external_access_url || payload.effective_url || ''
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

async function update(value: string | null) {
  if (!meta.value?.editable) return
  saving.value = true
  validationError.value = ''
  try {
    const payload =
      value === null ? null : value.trim()
    applyPayload(await patchPlatformExternalAccess(payload))
    clearDeployProfileCache()
    try {
      await fetchDeployProfile(true)
    } catch {
      // Profile refresh is best-effort; the URL save already succeeded.
    }
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

function save() {
  const trimmed = externalAccessUrl.value.trim()
  const effective = (meta.value?.effective_url || '').trim()
  // Empty / unchanged without a Runtime row must not create an override that
  // blocks Deployment. Restore (null) clears; intentional empty only after
  // an override already exists.
  if (!hasOverride.value) {
    if (!trimmed || trimmed === effective) return
  }
  void update(trimmed)
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
      class="platform-settings platform-settings--stacked"
    >
      <template v-if="meta">
        <el-alert
          v-if="!meta.editable"
          type="info"
          show-icon
          :closable="false"
          class="platform-settings__alert"
          :title="t('platformOps.settings.externalAccess.managed')"
        />

        <div class="platform-settings__stack">
          <section class="platform-settings__panel">
            <header class="platform-settings__panel-head">
              <h3>{{ t('platformOps.settings.externalAccess.configurationTitle') }}</h3>
            </header>
            <div class="platform-settings__rows">
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.externalAccess.urlLabel') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-input
                    v-model="externalAccessUrl"
                    type="url"
                    autocomplete="url"
                    maxlength="2048"
                    :disabled="!meta.editable || saving"
                    :placeholder="t('platformOps.settings.externalAccess.urlPlaceholder')"
                    @input="validationError = ''"
                  />
                  <p class="platform-settings__hint">
                    {{ t('platformOps.settings.externalAccess.urlHint') }}
                  </p>
                  <p class="platform-settings__hint">
                    {{ t('platformOps.settings.externalAccess.impact') }}
                  </p>
                  <p
                    v-if="validationError"
                    class="platform-settings__hint platform-settings__hint--error"
                  >
                    {{ validationError }}
                  </p>
                </div>
              </div>
            </div>
          </section>

          <div
            v-if="meta.editable"
            class="platform-settings__actions"
          >
            <el-button
              :disabled="saving || !hasOverride"
              @click="update(null)"
            >
              {{ t('platformOps.settings.externalAccess.restoreDefaults') }}
            </el-button>
            <el-button
              type="primary"
              :loading="saving"
              :disabled="busy"
              @click="save"
            >
              {{ t('platformOps.settings.saveChanges') }}
            </el-button>
          </div>
        </div>
      </template>
    </div>
  </ModulePage>
</template>

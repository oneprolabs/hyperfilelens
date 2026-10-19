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
const productWebsiteUrl = ref('')
const websiteSupported = computed(() => meta.value?.product_website_url !== undefined)
const hasOverride = computed(
  () => meta.value?.has_runtime_override === true || meta.value?.source === 'runtime',
)
const hasWebsiteOverride = computed(() => websiteSupported.value
  && Boolean(meta.value?.product_website_url?.trim()))
const canRestoreDefaults = computed(() => hasOverride.value || hasWebsiteOverride.value)
const externalChanged = computed(() => {
  const value = externalAccessUrl.value.trim()
  if (!hasOverride.value && !value) return false
  return value !== (meta.value?.external_access_url || meta.value?.effective_url || '').trim()
})
const websiteChanged = computed(() => websiteSupported.value
  && productWebsiteUrl.value.trim()
    !== (meta.value?.product_website_url || meta.value?.default_product_website_url || '').trim())

function applyPayload(payload: PlatformExternalAccessSettings) {
  meta.value = payload
  // Show the URL currently in effect (Runtime override, else Deployment/Default).
  externalAccessUrl.value = payload.external_access_url || payload.effective_url || ''
  productWebsiteUrl.value = payload.product_website_url || payload.default_product_website_url || ''
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

async function update(value: string | null | undefined, website?: string) {
  if (!meta.value?.editable) return
  saving.value = true
  validationError.value = ''
  const keepExternalDraft = value === undefined && externalChanged.value
  const externalDraft = externalAccessUrl.value
  const keepWebsiteDraft = website === undefined && websiteChanged.value
  const websiteDraft = productWebsiteUrl.value
  try {
    const payload =
      value == null ? value : value.trim()
    applyPayload(await patchPlatformExternalAccess(payload, website))
    // Preserve unsaved edits to fields not included in this update.
    if (keepExternalDraft) externalAccessUrl.value = externalDraft
    if (keepWebsiteDraft) productWebsiteUrl.value = websiteDraft
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
  if (!externalChanged.value && !websiteChanged.value) return
  void update(
    externalChanged.value ? externalAccessUrl.value.trim() : undefined,
    websiteChanged.value ? productWebsiteUrl.value.trim() : undefined,
  )
}

function restoreDefaults() {
  if (busy.value || saving.value || !canRestoreDefaults.value) return
  void update(
    hasOverride.value ? null : undefined,
    hasWebsiteOverride.value ? '' : undefined,
  )
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
                </div>
              </div>
              <div
                v-if="websiteSupported"
                class="platform-settings__setting-row"
              >
                <div class="platform-settings__setting-label">
                  <label for="product-website-url">{{ t('platformOps.settings.externalAccess.productWebsiteUrl') }}</label>
                </div>
                <div class="platform-settings__setting-control">
                  <el-input
                    id="product-website-url"
                    v-model="productWebsiteUrl"
                    type="url"
                    autocomplete="url"
                    maxlength="2048"
                    :disabled="!meta.editable || saving"
                    :placeholder="meta.default_product_website_url || ''"
                    @input="validationError = ''"
                  />
                  <p class="platform-settings__hint">
                    {{ t('platformOps.settings.externalAccess.productWebsiteHint', { url: meta.default_product_website_url || '' }) }}
                  </p>
                </div>
              </div>
            </div>
            <p
              v-if="validationError"
              class="platform-settings__hint platform-settings__hint--error"
              role="alert"
            >
              {{ validationError }}
            </p>
          </section>

          <div
            v-if="meta.editable"
            class="platform-settings__actions"
          >
            <el-button
              :disabled="busy || saving || !canRestoreDefaults"
              @click="restoreDefaults"
            >
              {{ t('platformOps.settings.externalAccess.restoreDefaults') }}
            </el-button>
            <el-button
              type="primary"
              :loading="saving"
              :disabled="busy || (!externalChanged && !websiteChanged)"
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

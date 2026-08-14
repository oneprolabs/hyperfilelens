<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import ModulePage from '../../../../components/ModulePage.vue'
import PlatformOpsRefreshButton from '../../../components/PlatformOpsRefreshButton.vue'
import DangerConfirmDialog from '../../../../components/DangerConfirmDialog.vue'
import { useResolvedPlatformOpsSideNav } from '../../../composables/useResolvedPlatformOpsSideNav'
import {
  fetchPlatformIdentitySettings,
  patchPlatformIdentitySettings,
  type PlatformIdentitySettings,
} from '../../../lib/platformOpsApi'
import { apiErrorMessage } from '../../../../lib/api'

const { t } = useI18n()
const sideNav = useResolvedPlatformOpsSideNav()

const busy = ref(false)
const saving = ref(false)
const meta = ref<PlatformIdentitySettings | null>(null)
const disableConfirmOpen = ref(false)
const enterpriseIdentityEnabled = computed(() => Boolean(meta.value?.enterprise_identity_enabled))
const platformOpsManaged = computed(() => meta.value?.platform_ops_source === 'deployment')
const disablesPlatformOps = computed(() => Boolean(meta.value?.platform_ops_enabled && !form.platform_ops_enabled))
const form = reactive({
  email_signup_enabled: false,
  email_code_login_enabled: false,
  platform_ops_enabled: true,
  platform_ops_allowed_cidrs: '',
  google_oauth_enabled: false,
  google_client_id: '',
  google_client_secret: '',
  turnstile_site_key: '',
  turnstile_secret_key: '',
  registration_verification_code_minutes: 10,
  registration_token_expiry_hours: 24,
  password_reset_verification_code_minutes: 10,
  password_reset_timeout_seconds: 3600,
  login_verification_code_minutes: 10,
})

async function load() {
  busy.value = true
  try {
    const data = await fetchPlatformIdentitySettings()
    meta.value = data
    form.email_signup_enabled = data.email_signup_enabled
    form.email_code_login_enabled = data.email_code_login_enabled
    form.platform_ops_enabled = data.platform_ops_enabled
    form.platform_ops_allowed_cidrs = (data.platform_ops_allowed_cidrs || []).join(', ')
    form.google_oauth_enabled = data.google_oauth_enabled
    form.google_client_id = data.google_client_id || ''
    form.google_client_secret = ''
    form.turnstile_site_key = data.turnstile_site_key || ''
    form.turnstile_secret_key = ''
    form.registration_verification_code_minutes = data.iam.registration_verification_code_minutes
    form.registration_token_expiry_hours = data.iam.registration_token_expiry_hours
    form.password_reset_verification_code_minutes = data.iam.password_reset_verification_code_minutes
    form.password_reset_timeout_seconds = data.iam.password_reset_timeout_seconds
    form.login_verification_code_minutes = data.iam.login_verification_code_minutes
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.loadFailed')), grouping: true })
  } finally {
    busy.value = false
  }
}

async function performSave(confirmDisable = false) {
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      platform_ops_enabled: form.platform_ops_enabled,
      platform_ops_allowed_cidrs: form.platform_ops_allowed_cidrs,
    }
    if (enterpriseIdentityEnabled.value) {
      body.email_signup_enabled = form.email_signup_enabled
      body.email_code_login_enabled = form.email_code_login_enabled
      body.google_oauth_enabled = form.google_oauth_enabled
      body.google_client_id = form.google_client_id
      if (form.google_client_secret.trim()) body.google_client_secret = form.google_client_secret
      body.turnstile_site_key = form.turnstile_site_key
      if (form.turnstile_secret_key.trim()) body.turnstile_secret_key = form.turnstile_secret_key
      body.iam = {
        registration_verification_code_minutes: form.registration_verification_code_minutes,
        registration_token_expiry_hours: form.registration_token_expiry_hours,
        password_reset_verification_code_minutes: form.password_reset_verification_code_minutes,
        password_reset_timeout_seconds: form.password_reset_timeout_seconds,
        login_verification_code_minutes: form.login_verification_code_minutes,
      }
    }
    if (confirmDisable) body.confirm_disable = 'DISABLE'
    meta.value = await patchPlatformIdentitySettings(body)
    form.google_client_secret = ''
    form.turnstile_secret_key = ''
    ElMessage.success({ message: t('platformOps.settings.saveSuccess'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.saveFailed')), grouping: true })
  } finally {
    saving.value = false
  }
}

function save() {
  if (disablesPlatformOps.value) {
    disableConfirmOpen.value = true
    return
  }
  void performSave()
}

async function confirmDisable() {
  await performSave(true)
  disableConfirmOpen.value = false
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
      class="platform-settings"
    >
      <el-alert
        v-if="meta && !enterpriseIdentityEnabled"
        type="info"
        :closable="false"
        show-icon
        class="platform-settings__alert"
        :title="t('platformOps.settings.identity.extensionRequiredTitle')"
        :description="t('platformOps.settings.identity.extensionRequiredBody')"
      />

      <el-form
        label-position="top"
        class="platform-settings__form"
      >
        <el-form-item :label="t('platformOps.settings.identity.platformOps')">
          <el-switch
            v-model="form.platform_ops_enabled"
            :disabled="platformOpsManaged"
          />
          <p
            v-if="platformOpsManaged"
            class="platform-settings__hint"
          >
            Admin Console availability is managed by deployment configuration and is read-only here.
          </p>
        </el-form-item>
        <el-form-item :label="t('platformOps.settings.identity.opsCidrs')">
          <el-input
            v-model="form.platform_ops_allowed_cidrs"
            :placeholder="t('platformOps.settings.identity.opsCidrsHint')"
          />
          <p class="platform-settings__hint">
            Restrict access to trusted operator networks. Recovery requires deployment or database access if all operators are locked out.
          </p>
        </el-form-item>

        <el-alert
          v-if="disablesPlatformOps"
          type="error"
          :closable="false"
          show-icon
          title="Admin Console access will be disabled"
        >
          Saving this change ends normal operator access after the current request. Confirm that deployment or database recovery access is available before continuing.
        </el-alert>

        <template v-if="enterpriseIdentityEnabled">
          <h3 class="platform-settings__section">
            {{ t('platformOps.settings.identity.tenantAuthTitle') }}
          </h3>
          <el-form-item :label="t('platformOps.settings.identity.emailSignup')">
            <el-switch v-model="form.email_signup_enabled" />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.emailCodeLogin')">
            <el-switch v-model="form.email_code_login_enabled" />
            <p class="platform-settings__hint">
              {{ t('platformOps.settings.identity.emailCodeLoginHint') }}
            </p>
          </el-form-item>

          <h3 class="platform-settings__section">
            {{ t('platformOps.settings.googleOAuthTitle') }}
          </h3>
          <p class="platform-settings__intro">
            {{ t('platformOps.settings.googleOAuth.intro') }}
          </p>
          <el-form-item :label="t('platformOps.settings.identity.googleOAuthEnabled')">
            <el-switch v-model="form.google_oauth_enabled" />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.googleClientId')">
            <el-input
              v-model="form.google_client_id"
              autocomplete="off"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.googleClientSecret')">
            <el-input
              v-model="form.google_client_secret"
              type="password"
              show-password
              autocomplete="new-password"
              :placeholder="meta?.google_client_secret_configured ? '••••••••' : ''"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.googleRedirect')">
            <el-input
              :model-value="meta?.google_oauth_redirect_uri || '—'"
              disabled
            />
            <p class="platform-settings__hint">
              {{ t('platformOps.settings.googleOAuth.redirectHint') }}
            </p>
          </el-form-item>

          <h3 class="platform-settings__section">
            {{ t('platformOps.settings.turnstileTitle') }}
          </h3>
          <p class="platform-settings__intro">
            {{ t('platformOps.settings.turnstile.intro') }}
          </p>
          <el-form-item :label="t('platformOps.settings.turnstile.statusLabel')">
            <el-tag :type="meta?.turnstile_enabled ? 'success' : 'info'">
              {{
                meta?.turnstile_enabled
                  ? t('platformOps.settings.turnstile.enabled')
                  : t('platformOps.settings.turnstile.disabled')
              }}
            </el-tag>
            <p class="platform-settings__hint">
              {{ t('platformOps.settings.turnstile.enableHint') }}
            </p>
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.turnstileSiteKey')">
            <el-input
              v-model="form.turnstile_site_key"
              autocomplete="off"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.turnstileSecret')">
            <el-input
              v-model="form.turnstile_secret_key"
              type="password"
              show-password
              autocomplete="new-password"
              :placeholder="meta?.turnstile_secret_configured ? '••••••••' : ''"
            />
            <p class="platform-settings__hint">
              {{ t('platformOps.settings.turnstile.secretHint') }}
            </p>
          </el-form-item>

          <h3 class="platform-settings__section">
            {{ t('platformOps.settings.identity.iamTitle') }}
          </h3>
          <el-form-item :label="t('platformOps.settings.identity.regCodeMinutes')">
            <el-input-number
              v-model="form.registration_verification_code_minutes"
              :min="1"
              :max="120"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.regTokenHours')">
            <el-input-number
              v-model="form.registration_token_expiry_hours"
              :min="1"
              :max="168"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.resetCodeMinutes')">
            <el-input-number
              v-model="form.password_reset_verification_code_minutes"
              :min="1"
              :max="120"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.resetTimeoutSeconds')">
            <el-input-number
              v-model="form.password_reset_timeout_seconds"
              :min="60"
              :max="86400"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.identity.loginCodeMinutes')">
            <el-input-number
              v-model="form.login_verification_code_minutes"
              :min="1"
              :max="30"
            />
          </el-form-item>
        </template>
      </el-form>

      <div class="platform-settings__footer">
        <PlatformOpsRefreshButton
          :loading="busy"
          @click="load"
        />
        <el-button
          type="primary"
          :loading="saving"
          @click="save"
        >
          {{ t('common.save') }}
        </el-button>
      </div>
    </div>
    <DangerConfirmDialog
      v-model="disableConfirmOpen"
      title="Disable Admin Console"
      message="Disable Admin Console access for all operators? Recovery may require changing deployment configuration or the platform database outside this console."
      confirm-mode="keyword"
      confirm-keyword="DISABLE"
      confirm-text="Disable Admin Console"
      :cancel-text="t('common.cancel')"
      :loading="saving"
      @confirm="confirmDisable"
    />
  </ModulePage>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import ModulePage from '../../../../components/ModulePage.vue'
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
const enterpriseIdentityEnabled = computed(() => Boolean(meta.value?.enterprise_identity_enabled))
const hasRuntimeOverride = computed(() => Boolean(meta.value?.has_runtime_override))
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
  registration_verification_code_minutes: 15,
  registration_token_expiry_minutes: 1440,
  password_reset_verification_code_minutes: 10,
  password_reset_timeout_minutes: 60,
  login_verification_code_minutes: 10,
})

function applyPayload(data: PlatformIdentitySettings) {
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
  form.registration_token_expiry_minutes = data.iam.registration_token_expiry_minutes
  form.password_reset_verification_code_minutes = data.iam.password_reset_verification_code_minutes
  form.password_reset_timeout_minutes = data.iam.password_reset_timeout_minutes
  form.login_verification_code_minutes = data.iam.login_verification_code_minutes
}

async function load() {
  busy.value = true
  try {
    applyPayload(await fetchPlatformIdentitySettings())
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.loadFailed')), grouping: true })
  } finally {
    busy.value = false
  }
}

async function save() {
  saving.value = true
  try {
    const body: Record<string, unknown> = {
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
        registration_token_expiry_minutes: form.registration_token_expiry_minutes,
        password_reset_verification_code_minutes: form.password_reset_verification_code_minutes,
        password_reset_timeout_minutes: form.password_reset_timeout_minutes,
        login_verification_code_minutes: form.login_verification_code_minutes,
      }
    }
    applyPayload(await patchPlatformIdentitySettings(body))
    ElMessage.success({ message: t('platformOps.settings.saveSuccess'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.saveFailed')), grouping: true })
  } finally {
    saving.value = false
  }
}

async function restoreDefaults() {
  if (!hasRuntimeOverride.value) return
  saving.value = true
  try {
    applyPayload(await patchPlatformIdentitySettings({ clear_runtime: true }))
    ElMessage.success({ message: t('platformOps.settings.saveSuccess'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.saveFailed')), grouping: true })
  } finally {
    saving.value = false
  }
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
      class="platform-settings platform-settings--identity platform-settings--stacked"
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

      <div class="platform-settings__stack">
        <section class="platform-settings__panel">
          <header class="platform-settings__panel-head">
            <h3>{{ t('platformOps.settings.identity.adminConsoleTitle') }}</h3>
          </header>
          <div class="platform-settings__rows">
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.identity.platformOps') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <el-switch
                  v-model="form.platform_ops_enabled"
                  disabled
                />
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.identity.opsCidrs') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <el-input
                  v-model="form.platform_ops_allowed_cidrs"
                  :disabled="saving"
                  :placeholder="t('platformOps.settings.identity.opsCidrsHint')"
                />
                <p class="platform-settings__hint">
                  {{ t('platformOps.settings.identity.opsCidrsHelp') }}
                </p>
              </div>
            </div>
          </div>
        </section>

        <template v-if="enterpriseIdentityEnabled">
          <section class="platform-settings__panel">
            <header class="platform-settings__panel-head">
              <h3>{{ t('platformOps.settings.identity.tenantAuthTitle') }}</h3>
            </header>
            <div class="platform-settings__rows">
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.emailCodeLogin') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-switch
                    v-model="form.email_code_login_enabled"
                    :disabled="saving"
                  />
                  <p class="platform-settings__hint">
                    {{ t('platformOps.settings.identity.emailServiceRequired') }}
                  </p>
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.emailSignup') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-switch
                    v-model="form.email_signup_enabled"
                    :disabled="saving"
                  />
                  <p class="platform-settings__hint">
                    {{ t('platformOps.settings.identity.emailServiceRequired') }}
                  </p>
                </div>
              </div>
            </div>
          </section>

          <section class="platform-settings__panel">
            <header class="platform-settings__panel-head">
              <h3>{{ t('platformOps.settings.googleOAuthTitle') }}</h3>
            </header>
            <div class="platform-settings__rows">
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.googleOAuthEnabled') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-switch
                    v-model="form.google_oauth_enabled"
                    :disabled="saving"
                  />
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.googleClientId') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-input
                    v-model="form.google_client_id"
                    autocomplete="off"
                    :disabled="saving"
                  />
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.googleClientSecret') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-input
                    v-model="form.google_client_secret"
                    type="password"
                    show-password
                    autocomplete="new-password"
                    :disabled="saving"
                    :placeholder="meta?.google_client_secret_configured ? '••••••••' : ''"
                  />
                  <p class="platform-settings__hint">
                    {{ t('platformOps.settings.identity.secretKeepHint') }}
                  </p>
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.googleRedirect') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-input
                    :model-value="meta?.google_oauth_redirect_uri || '—'"
                    disabled
                  />
                  <p class="platform-settings__hint">
                    {{ t('platformOps.settings.googleOAuth.redirectHint') }}
                  </p>
                </div>
              </div>
            </div>
          </section>

          <section class="platform-settings__panel">
            <header class="platform-settings__panel-head">
              <h3>{{ t('platformOps.settings.turnstileTitle') }}</h3>
            </header>
            <div class="platform-settings__rows">
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.turnstile.enabled') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-switch
                    :model-value="Boolean(meta?.turnstile_enabled)"
                    disabled
                  />
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.turnstileSiteKey') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-input
                    v-model="form.turnstile_site_key"
                    autocomplete="off"
                    :disabled="saving"
                  />
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.turnstileSecret') }}</span>
                </div>
                <div class="platform-settings__setting-control">
                  <el-input
                    v-model="form.turnstile_secret_key"
                    type="password"
                    show-password
                    autocomplete="new-password"
                    :disabled="saving"
                    :placeholder="meta?.turnstile_secret_configured ? '••••••••' : ''"
                  />
                  <p class="platform-settings__hint">
                    {{ t('platformOps.settings.turnstile.secretHint') }}
                  </p>
                </div>
              </div>
            </div>
          </section>

          <section class="platform-settings__panel">
            <header class="platform-settings__panel-head">
              <h3>{{ t('platformOps.settings.identity.iamTitle') }}</h3>
            </header>
            <div class="platform-settings__rows">
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.loginCodeMinutes') }}</span>
                </div>
                <div class="platform-settings__setting-control platform-settings__setting-control--compact">
                  <el-input-number
                    v-model="form.login_verification_code_minutes"
                    :min="1"
                    :max="30"
                    :disabled="saving"
                    controls-position="right"
                  />
                  <span class="platform-settings__unit">{{ t('platformOps.settings.identity.unitMinutes') }}</span>
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.regCodeMinutes') }}</span>
                </div>
                <div class="platform-settings__setting-control platform-settings__setting-control--compact">
                  <el-input-number
                    v-model="form.registration_verification_code_minutes"
                    :min="1"
                    :max="120"
                    :disabled="saving"
                    controls-position="right"
                  />
                  <span class="platform-settings__unit">{{ t('platformOps.settings.identity.unitMinutes') }}</span>
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.regTokenMinutes') }}</span>
                </div>
                <div class="platform-settings__setting-control platform-settings__setting-control--compact">
                  <el-input-number
                    v-model="form.registration_token_expiry_minutes"
                    :min="60"
                    :max="10080"
                    :disabled="saving"
                    controls-position="right"
                  />
                  <span class="platform-settings__unit">{{ t('platformOps.settings.identity.unitMinutes') }}</span>
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.resetCodeMinutes') }}</span>
                </div>
                <div class="platform-settings__setting-control platform-settings__setting-control--compact">
                  <el-input-number
                    v-model="form.password_reset_verification_code_minutes"
                    :min="1"
                    :max="120"
                    :disabled="saving"
                    controls-position="right"
                  />
                  <span class="platform-settings__unit">{{ t('platformOps.settings.identity.unitMinutes') }}</span>
                </div>
              </div>
              <div class="platform-settings__setting-row">
                <div class="platform-settings__setting-label">
                  <span>{{ t('platformOps.settings.identity.resetTimeoutMinutes') }}</span>
                </div>
                <div class="platform-settings__setting-control platform-settings__setting-control--compact">
                  <el-input-number
                    v-model="form.password_reset_timeout_minutes"
                    :min="1"
                    :max="1440"
                    :disabled="saving"
                    controls-position="right"
                  />
                  <span class="platform-settings__unit">{{ t('platformOps.settings.identity.unitMinutes') }}</span>
                </div>
              </div>
            </div>
          </section>
        </template>

        <div class="platform-settings__actions">
          <el-button
            :loading="saving"
            :disabled="busy || !hasRuntimeOverride"
            @click="restoreDefaults"
          >
            {{ t('platformOps.settings.identity.restoreDefaults') }}
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
    </div>
  </ModulePage>
</template>

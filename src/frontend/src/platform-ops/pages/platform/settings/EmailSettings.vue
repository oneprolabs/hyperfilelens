<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import ModulePage from '../../../../components/ModulePage.vue'
import { useResolvedPlatformOpsSideNav } from '../../../composables/useResolvedPlatformOpsSideNav'
import {
  fetchPlatformEmailSettings,
  patchPlatformEmailSettings,
  testPlatformEmail,
  type PlatformEmailSettings,
} from '../../../lib/platformOpsApi'
import { apiErrorMessage } from '../../../../lib/api'

const { t } = useI18n()
const sideNav = useResolvedPlatformOpsSideNav()

const busy = ref(false)
const saving = ref(false)
const testing = ref(false)
const testRecipient = ref('')
const meta = ref<PlatformEmailSettings | null>(null)
const form = reactive({
  host: '',
  port: 587,
  use_tls: true,
  use_ssl: false,
  host_user: '',
  password: '',
  from_email: '',
})

const hasRuntimeOverride = computed(() => !!meta.value?.has_runtime_override || meta.value?.source === 'runtime')
const enterpriseIdentityEnabled = computed(() => Boolean(meta.value?.enterprise_identity_enabled))
// Deployment SMTP is a hint only; Runtime may still override (R > D > Def).
const emailEditable = computed(() => enterpriseIdentityEnabled.value)

type EncryptionMode = 'none' | 'starttls' | 'ssl'

const encryption = computed<EncryptionMode>({
  get() {
    if (form.use_ssl) return 'ssl'
    if (form.use_tls) return 'starttls'
    return 'none'
  },
  set(mode) {
    if (mode === 'ssl') {
      form.use_ssl = true
      form.use_tls = false
      return
    }
    if (mode === 'starttls') {
      form.use_ssl = false
      form.use_tls = true
      return
    }
    form.use_ssl = false
    form.use_tls = false
  },
})

const encryptionLabel = computed(() => {
  if (encryption.value === 'ssl') return t('platformOps.settings.email.transportSsl')
  if (encryption.value === 'starttls') return t('platformOps.settings.email.transportTls')
  return t('platformOps.settings.email.transportNone')
})

function applyPayload(data: PlatformEmailSettings) {
  meta.value = data
  form.host = data.host || ''
  form.port = data.port || 587
  form.use_tls = !!data.use_tls
  form.use_ssl = !!data.use_ssl
  form.host_user = data.host_user || ''
  form.from_email = data.from_email || ''
  form.password = ''
}

async function load() {
  busy.value = true
  try {
    applyPayload(await fetchPlatformEmailSettings())
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.loadFailed')), grouping: true })
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!emailEditable.value) return
  saving.value = true
  try {
    const body: Record<string, unknown> = {
      host: form.host,
      port: form.port,
      use_tls: form.use_tls,
      use_ssl: form.use_ssl,
      host_user: form.host_user,
      from_email: form.from_email,
    }
    if (form.password.trim()) body.password = form.password
    applyPayload(await patchPlatformEmailSettings(body))
    ElMessage.success({ message: t('platformOps.settings.saveSuccess'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.saveFailed')), grouping: true })
  } finally {
    saving.value = false
  }
}


async function restoreDeployment() {
  if (!emailEditable.value || !hasRuntimeOverride.value) return
  saving.value = true
  try {
    applyPayload(await patchPlatformEmailSettings({ clear_runtime: true }))
    ElMessage.success({ message: t('platformOps.settings.saveSuccess'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.saveFailed')), grouping: true })
  } finally {
    saving.value = false
  }
}

async function sendTest() {
  const recipient = testRecipient.value.trim()
  if (!recipient) {
    ElMessage.warning({ message: t('platformOps.settings.email.testRecipientRequired'), grouping: true })
    return
  }
  testing.value = true
  try {
    const result = await testPlatformEmail(recipient)
    if (result.ok) {
      ElMessage.success({
        message: t('platformOps.settings.email.testSuccess', {
          recipient: result.recipient || recipient,
        }),
        grouping: true,
      })
    } else {
      ElMessage.error({ message: result.error || t('platformOps.settings.email.testFailed'), grouping: true })
    }
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('platformOps.settings.email.testFailed')), grouping: true })
  } finally {
    testing.value = false
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
      class="platform-settings platform-settings--stacked"
    >
      <template v-if="meta">
      <el-alert
        v-if="!enterpriseIdentityEnabled"
        type="info"
        :closable="false"
        show-icon
        class="platform-settings__alert"
        :title="t('platformOps.settings.email.extensionRequiredTitle')"
        :description="t('platformOps.settings.email.extensionRequiredBody')"
      />

      <el-alert
        v-if="enterpriseIdentityEnabled && !meta.delivery_configured"
        type="warning"
        :closable="false"
        show-icon
        class="platform-settings__alert"
        :title="t('platformOps.settings.email.deliveryUnavailable')"
        :description="meta.configuration_error || undefined"
      />

      <div class="platform-settings__stack">
        <section class="platform-settings__panel">
          <header class="platform-settings__panel-head">
            <h3>{{ t('platformOps.settings.email.smtpTitle') }}</h3>
          </header>

          <div
            v-if="!emailEditable"
            class="platform-settings__rows"
          >
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.host') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <span>{{ form.host || '—' }}</span>
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.port') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <span>{{ form.port || '—' }}</span>
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.hostUser') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <span>{{ form.host_user || '—' }}</span>
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.password') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <span>{{
                  meta?.password_configured
                    ? t('platformOps.settings.email.passwordConfigured')
                    : t('platformOps.settings.email.passwordNotConfigured')
                }}</span>
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.fromEmail') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <span>{{ form.from_email || '—' }}</span>
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.encryption') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <span>{{ encryptionLabel }}</span>
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.configurationSource') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <span>{{ meta?.source || '—' }}</span>
              </div>
            </div>
          </div>

          <div
            v-else
            class="platform-settings__rows"
          >
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.host') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <el-input
                  v-model="form.host"
                  autocomplete="off"
                  :disabled="saving"
                />
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.port') }}</span>
              </div>
              <div class="platform-settings__setting-control platform-settings__setting-control--compact">
                <el-input-number
                  v-model="form.port"
                  :min="1"
                  :max="65535"
                  :disabled="saving"
                  controls-position="right"
                />
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.hostUser') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <el-input
                  v-model="form.host_user"
                  autocomplete="off"
                  :disabled="saving"
                />
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.password') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <el-input
                  v-model="form.password"
                  type="password"
                  show-password
                  autocomplete="new-password"
                  :disabled="saving"
                  :placeholder="meta?.password_configured ? '••••••••' : ''"
                />
                <p class="platform-settings__hint">
                  {{ t('platformOps.settings.email.passwordHint') }}
                </p>
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.fromEmail') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <el-input
                  v-model="form.from_email"
                  autocomplete="off"
                  :disabled="saving"
                />
              </div>
            </div>
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.encryption') }}</span>
              </div>
              <div class="platform-settings__setting-control platform-settings__setting-control--compact">
                <el-select
                  v-model="encryption"
                  popper-class="platform-settings__select-dropdown"
                  :disabled="saving"
                >
                  <el-option
                    value="none"
                    :label="t('platformOps.settings.email.transportNone')"
                  />
                  <el-option
                    value="starttls"
                    :label="t('platformOps.settings.email.transportTls')"
                  />
                  <el-option
                    value="ssl"
                    :label="t('platformOps.settings.email.transportSsl')"
                  />
                </el-select>
              </div>
            </div>
          </div>
        </section>

        <section class="platform-settings__panel">
          <header class="platform-settings__panel-head">
            <h3>{{ t('platformOps.settings.email.testSectionTitle') }}</h3>
          </header>
          <div class="platform-settings__rows">
            <div class="platform-settings__setting-row">
              <div class="platform-settings__setting-label">
                <span>{{ t('platformOps.settings.email.testRecipient') }}</span>
              </div>
              <div class="platform-settings__setting-control">
                <div class="platform-settings__inline-actions">
                  <el-input
                    v-model="testRecipient"
                    :placeholder="t('platformOps.settings.email.testRecipientPlaceholder')"
                    autocomplete="off"
                    :disabled="testing"
                  />
                  <el-button
                    :loading="testing"
                    @click="sendTest"
                  >
                    {{ t('platformOps.settings.email.testSend') }}
                  </el-button>
                </div>
              </div>
            </div>
          </div>
        </section>

        <div
          v-if="emailEditable"
          class="platform-settings__actions"
        >
          <el-button
            :loading="saving"
            :disabled="busy || !hasRuntimeOverride"
            @click="restoreDeployment"
          >
            {{ t('platformOps.settings.email.restoreDefaults') }}
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

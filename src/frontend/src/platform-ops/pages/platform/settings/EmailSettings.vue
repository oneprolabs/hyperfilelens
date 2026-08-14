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
const managedByDeployment = computed(() => !!meta.value?.managed_by_deployment)
const enterpriseIdentityEnabled = computed(() => Boolean(meta.value?.enterprise_identity_enabled))
const emailEditable = computed(() => enterpriseIdentityEnabled.value && !managedByDeployment.value)
const deliveryStatusType = computed(() => (
  meta.value?.delivery_configured ? 'success' : 'warning'
))
const form = reactive({
  backend: '',
  host: '',
  port: 587,
  use_tls: true,
  use_ssl: false,
  host_user: '',
  password: '',
  from_email: '',
})

async function load() {
  busy.value = true
  try {
    const data = await fetchPlatformEmailSettings()
    meta.value = data
    form.backend = data.backend || ''
    form.host = data.host || ''
    form.port = data.port || 587
    form.use_tls = !!data.use_tls
    form.use_ssl = !!data.use_ssl
    form.host_user = data.host_user || ''
    form.from_email = data.from_email || ''
    form.password = ''
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
      backend: form.backend,
      host: form.host,
      port: form.port,
      use_tls: form.use_tls,
      use_ssl: form.use_ssl,
      host_user: form.host_user,
      from_email: form.from_email,
    }
    if (form.password.trim()) body.password = form.password
    meta.value = await patchPlatformEmailSettings(body)
    form.password = ''
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
      ElMessage.success({ message: t('platformOps.settings.email.testSuccess', { recipient: result.recipient || recipient }), grouping: true })
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
      class="platform-settings"
    >
      <div class="platform-settings__toolbar">
        <p class="platform-settings__intro">
          {{ t('platformOps.settings.email.intro') }}
        </p>
        <el-button
          v-if="emailEditable"
          type="primary"
          :loading="saving"
          @click="save"
        >
          {{ t('platformOps.settings.saveChanges') }}
        </el-button>
      </div>

      <div class="platform-settings__panel">
        <el-alert
          v-if="meta && !enterpriseIdentityEnabled"
          type="info"
          :closable="false"
          show-icon
          class="platform-settings__alert"
          :title="t('platformOps.settings.email.extensionRequiredTitle')"
          :description="t('platformOps.settings.email.extensionRequiredBody')"
        />

        <el-alert
          v-if="meta && enterpriseIdentityEnabled"
          :type="deliveryStatusType"
          :closable="false"
          show-icon
          :title="meta.delivery_configured
            ? t('platformOps.settings.email.deliveryReady')
            : t('platformOps.settings.email.deliveryUnavailable')"
        >
          <template #default>
            <p v-if="managedByDeployment">
              {{ t('platformOps.settings.email.managedByDeployment') }}
            </p>
            <p v-if="meta.configuration_error">
              {{ meta.configuration_error }}
            </p>
          </template>
        </el-alert>

        <div
          v-if="!emailEditable"
          class="platform-settings__readonly-summary hfl-detail-grid"
        >
          <div class="hfl-detail-row">
            <span class="hfl-detail-row__label">{{ t('platformOps.settings.email.backend') }}</span><strong
              class="hfl-detail-row__value"
              :class="{ 'hfl-detail-row__empty': !form.backend }"
            >{{ form.backend || '—' }}</strong>
          </div>
          <div class="hfl-detail-row">
            <span class="hfl-detail-row__label">{{ t('platformOps.settings.email.host') }}</span><strong
              class="hfl-detail-row__value"
              :class="{ 'hfl-detail-row__empty': !form.host }"
            >{{ form.host || '—' }}</strong>
          </div>
          <div class="hfl-detail-row">
            <span class="hfl-detail-row__label">{{ t('platformOps.settings.email.port') }}</span><strong
              class="hfl-detail-row__value"
              :class="{ 'hfl-detail-row__empty': !form.port }"
            >{{ form.port || '—' }}</strong>
          </div>
          <div class="hfl-detail-row">
            <span class="hfl-detail-row__label">{{ t('platformOps.settings.email.hostUser') }}</span><strong
              class="hfl-detail-row__value"
              :class="{ 'hfl-detail-row__empty': !form.host_user }"
            >{{ form.host_user || '—' }}</strong>
          </div>
          <div class="hfl-detail-row">
            <span class="hfl-detail-row__label">{{ t('platformOps.settings.email.password') }}</span><strong class="hfl-detail-row__value">{{ meta?.password_configured ? 'Configured' : 'Not configured' }}</strong>
          </div>
          <div class="hfl-detail-row">
            <span class="hfl-detail-row__label">{{ t('platformOps.settings.email.fromEmail') }}</span><strong
              class="hfl-detail-row__value"
              :class="{ 'hfl-detail-row__empty': !form.from_email }"
            >{{ form.from_email || '—' }}</strong>
          </div>
          <div class="hfl-detail-row">
            <span class="hfl-detail-row__label">Transport security</span><strong class="hfl-detail-row__value">{{ form.use_ssl ? 'SSL' : form.use_tls ? 'TLS' : 'None' }}</strong>
          </div>
          <div class="hfl-detail-row">
            <span class="hfl-detail-row__label">Configuration source</span>
            <strong class="hfl-detail-row__value">
              {{ managedByDeployment ? 'Deployment environment' : (meta?.source || '—') }}
            </strong>
          </div>
        </div>

        <el-form
          v-else
          label-position="top"
          class="platform-settings__form"
        >
          <el-form-item :label="t('platformOps.settings.email.backend')">
            <el-input
              v-model="form.backend"
              autocomplete="off"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.email.host')">
            <el-input
              v-model="form.host"
              autocomplete="off"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.email.port')">
            <el-input-number
              v-model="form.port"
              :min="1"
              :max="65535"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.email.hostUser')">
            <el-input
              v-model="form.host_user"
              autocomplete="off"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.email.password')">
            <el-input
              v-model="form.password"
              type="password"
              show-password
              autocomplete="new-password"
              :placeholder="meta?.password_configured ? '••••••••' : ''"
            />
            <p class="platform-settings__hint">
              {{ t('platformOps.settings.email.passwordHint') }}
            </p>
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.email.fromEmail')">
            <el-input
              v-model="form.from_email"
              autocomplete="off"
            />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.email.tls')">
            <el-switch v-model="form.use_tls" />
          </el-form-item>
          <el-form-item :label="t('platformOps.settings.email.ssl')">
            <el-switch v-model="form.use_ssl" />
          </el-form-item>
        </el-form>

        <div class="platform-settings__test-block">
          <h3 class="platform-settings__section">
            {{ t('platformOps.settings.email.testSectionTitle') }}
          </h3>
          <div class="platform-settings__test-row">
            <el-input
              v-model="testRecipient"
              :placeholder="t('platformOps.settings.email.testRecipient')"
              autocomplete="off"
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
  </ModulePage>
</template>

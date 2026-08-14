<script setup lang="ts">
import { ref, computed, onMounted, onActivated, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { KeyRound, ShieldCheck, UserRound } from 'lucide-vue-next'
import { useAuth } from '../../composables/useAuth'
import { api, apiErrorMessage } from '../../lib/api'
import { unwrapApiPayload } from '../../lib/parse'
import { formatAppDateTime } from '../../lib/dateTime'

type SecurityAudit = {
  last_login_at?: string | null
  last_login_ip?: string | null
  last_login_location?: string | null
  lastLoginAt?: string | null
  lastLoginIp?: string | null
  lastLoginLocation?: string | null
}

type AccountUserDetails = {
  registered_at?: string | null
  registeredAt?: string | null
  security_audit?: SecurityAudit | null
  securityAudit?: SecurityAudit | null
  timezone?: string | null
}

const { t } = useI18n()
const router = useRouter()
const { user, clearAuth } = useAuth()

const emptyValue = computed(() => t('account.emptyValue'))

const email = computed(() => {
  const value = user.value?.email?.trim()
  return value || emptyValue.value
})
const username = computed(() => {
  const value = user.value?.username?.trim()
  return value || email.value
})
const accountInitial = computed(() => {
  const source = username.value || email.value
  const first = source.trim().charAt(0)
  return first ? first.toUpperCase() : 'A'
})
const role = computed(() => {
  const r = user.value?.access_profile?.role
  if (!r) return '—'
  const roleMap: Record<string, string> = {
    owner: t('account.roleOwner'),
    admin: t('account.roleAdmin'),
    operator: t('account.roleOperator'),
    auditor: t('account.roleAuditor'),
  }
  return roleMap[r] || r
})

const registeredAt = ref('')
const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const currentPasswordError = ref('')
const newPasswordError = ref('')
const confirmPasswordError = ref('')
const currentPasswordTouched = ref(false)
const newPasswordTouched = ref(false)
const confirmPasswordTouched = ref(false)
const passwordSubmitting = ref(false)

const lastLoginAt = ref('')
const lastLoginIp = ref('')
const lastLoginLocationRaw = ref('')

const LOCAL_NETWORK_LOCATION_CODES = new Set(['local_network', 'Local network'])

const lastLoginLocation = computed(() => formatLoginLocation(lastLoginLocationRaw.value))

const userTimezone = computed(() => user.value?.timezone || 'Asia/Shanghai')

const passwordStrength = computed(() => {
  const pwd = newPassword.value
  if (!pwd) return { level: 0, text: '', color: '' }

  let score = 0
  if (pwd.length >= 8) score++
  if (pwd.length >= 12) score++
  if (/[a-z]/.test(pwd) && /[A-Z]/.test(pwd)) score++
  if (/\d/.test(pwd)) score++
  if (/[!"@#$%&'*():;\\+/=?^_`{|}~><-]/.test(pwd)) score++

  if (score <= 2) return { level: 1, text: t('login.passwordWeak'), color: 'var(--color-error)' }
  if (score <= 3) return { level: 2, text: t('login.passwordMedium'), color: 'var(--color-warning)' }
  return { level: 3, text: t('login.passwordStrong'), color: 'var(--color-success)' }
})

function isEmptyDisplay(value: string) {
  return !value || value === emptyValue.value
}

function checkPassword(value: string) {
  if (!value) {
    return t('account.newPasswordRequired')
  }
  const pattern = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d.!"@#$%&'*():;\\+/=?^_`{|}~><-]{8,20}$/
  if (!pattern.test(value)) {
    return t('account.securityPwdRule')
  }
  return ''
}

function validateCurrentPassword(showEmpty = false) {
  const error = currentPassword.value ? '' : t('account.currentPasswordRequired')
  currentPasswordError.value = error && (showEmpty || currentPasswordTouched.value) ? error : ''
  return !error
}

function validateNewPassword(showEmpty = false) {
  const error = checkPassword(newPassword.value)
  newPasswordError.value = error && (showEmpty || newPasswordTouched.value || Boolean(newPassword.value)) ? error : ''
  return !error
}

function validateConfirmPassword(showEmpty = false) {
  let error = ''
  if (!confirmPassword.value) {
    error = t('account.confirmPasswordRequired')
  } else if (newPassword.value !== confirmPassword.value) {
    error = t('account.securityPwdMismatch')
  }
  confirmPasswordError.value = error && (showEmpty || confirmPasswordTouched.value || Boolean(confirmPassword.value)) ? error : ''
  return !error
}

function validatePasswordForm(showEmpty = false) {
  const currentValid = validateCurrentPassword(showEmpty)
  const newValid = validateNewPassword(showEmpty)
  const confirmValid = validateConfirmPassword(showEmpty)
  return currentValid && newValid && confirmValid
}

function onCurrentPasswordInput() {
  if (currentPasswordTouched.value || currentPasswordError.value) {
    validateCurrentPassword(true)
  }
}

function onNewPasswordInput() {
  if (newPasswordTouched.value || newPasswordError.value) {
    validateNewPassword(true)
  }
  if (confirmPasswordTouched.value || confirmPassword.value) {
    validateConfirmPassword(false)
  }
}

function onConfirmPasswordInput() {
  confirmPasswordTouched.value = true
  validateConfirmPassword(true)
}

function resetPasswordFormState() {
  currentPassword.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
  currentPasswordError.value = ''
  newPasswordError.value = ''
  confirmPasswordError.value = ''
  currentPasswordTouched.value = false
  newPasswordTouched.value = false
  confirmPasswordTouched.value = false
}

function pickAudit(data: AccountUserDetails): SecurityAudit | null | undefined {
  return data.security_audit ?? data.securityAudit
}

function pickAuditValue(audit: SecurityAudit | null | undefined, snakeKey: keyof SecurityAudit, camelKey: keyof SecurityAudit) {
  const raw = audit?.[snakeKey] ?? audit?.[camelKey]
  if (raw === null || raw === undefined) return ''
  return String(raw).trim()
}

function pickRegisteredAt(data: AccountUserDetails) {
  return data.registered_at ?? data.registeredAt
}

function formatDateTime(value?: string | null, timeZone?: string) {
  if (!value) return ''
  return formatAppDateTime(value, '', { timeZone: timeZone || userTimezone.value })
}

function displayValue(value?: string | null) {
  const text = (value || '').trim()
  return text || emptyValue.value
}

function formatLoginLocation(value?: string | null) {
  const text = (value || '').trim()
  if (!text) return emptyValue.value
  if (LOCAL_NETWORK_LOCATION_CODES.has(text)) {
    return t('account.lastLoginLocationLocalNetwork')
  }
  return text
}

async function loadAccountDetails() {
  registeredAt.value = emptyValue.value
  lastLoginAt.value = emptyValue.value
  lastLoginIp.value = emptyValue.value
  lastLoginLocationRaw.value = ''

  try {
    const data = unwrapApiPayload<AccountUserDetails>(await api<unknown>('/api/v1/auth/user'))
    const tz = data.timezone || userTimezone.value
    registeredAt.value = displayValue(formatDateTime(pickRegisteredAt(data), tz))
    const audit = pickAudit(data)
    lastLoginAt.value = displayValue(formatDateTime(pickAuditValue(audit, 'last_login_at', 'lastLoginAt'), tz))
    lastLoginIp.value = displayValue(pickAuditValue(audit, 'last_login_ip', 'lastLoginIp'))
    lastLoginLocationRaw.value = pickAuditValue(audit, 'last_login_location', 'lastLoginLocation')
  } catch {
    registeredAt.value = emptyValue.value
    lastLoginAt.value = emptyValue.value
    lastLoginIp.value = emptyValue.value
    lastLoginLocationRaw.value = ''
  }
}

function applyPasswordFieldErrors(fields?: Record<string, string[]>) {
  if (!fields) return false
  let applied = false
  const first = (key: string) => fields[key]?.[0] || ''
  const currentError = first('current_password') || first('currentPassword')
  const newError = first('new_password') || first('newPassword') || first('password')
  const confirmError = first('confirm_password') || first('confirmPassword')

  if (currentError) {
    currentPasswordError.value = currentError
    currentPasswordTouched.value = true
    applied = true
  }
  if (newError) {
    newPasswordError.value = newError
    newPasswordTouched.value = true
    applied = true
  }
  if (confirmError) {
    confirmPasswordError.value = confirmError
    confirmPasswordTouched.value = true
    applied = true
  }
  return applied
}

async function updatePassword() {
  if (passwordSubmitting.value) return
  currentPasswordTouched.value = true
  newPasswordTouched.value = true
  confirmPasswordTouched.value = true
  if (!validatePasswordForm(true)) return

  passwordSubmitting.value = true
  try {
    const res = await api<{ code?: string; error?: { message?: string; fields?: Record<string, string[]> }; data?: { message?: string } }>(
      '/api/v1/auth/change-password',
      {
        method: 'POST',
        body: JSON.stringify({
          current_password: currentPassword.value,
          new_password: newPassword.value,
          confirm_password: confirmPassword.value,
        }),
      },
    )

    if (res.code && res.code !== '0000') {
      if (applyPasswordFieldErrors(res.error?.fields)) return
      ElMessage.error({ message: res.error?.message || t('account.passwordChangeFailed'), grouping: true })
      return
    }

    resetPasswordFormState()
    clearAuth()
    ElMessage.success({ message: res.data?.message || t('account.passwordChangedRelogin'), duration: 2500, grouping: true })
    await router.replace('/login')
  } catch (err: unknown) {
    const fields = err && typeof err === 'object' ? (err as { fields?: Record<string, string[]> }).fields : undefined
    if (!applyPasswordFieldErrors(fields)) {
      ElMessage.error({ message: apiErrorMessage(err, t('account.passwordChangeFailed')), grouping: true })
    }
  } finally {
    passwordSubmitting.value = false
  }
}

onMounted(() => {
  loadAccountDetails()
})

onActivated(() => {
  loadAccountDetails()
})

watch(
  () => user.value?.id,
  (id, prev) => {
    if (id && id !== prev) {
      loadAccountDetails()
    }
  },
)
</script>

<template>
  <div class="account-settings">
    <div class="account-settings__main">
      <section class="account-settings-section">
        <header class="account-settings-section__header">
          <span class="account-settings-section__icon account-settings-section__icon--account">
            <UserRound :size="16" />
          </span>
          <h2 class="account-settings-section__title">
            {{ t('account.profileSectionAccount') }}
          </h2>
        </header>
        <div class="account-overview">
          <div
            class="account-overview__avatar"
            aria-hidden="true"
          >
            {{ accountInitial }}
          </div>
          <div class="account-overview__content">
            <div class="account-overview__name">
              <span class="account-settings-row__text">{{ username }}</span>
              <span class="account-role-badge">{{ role }}</span>
            </div>
            <div class="account-overview__email">
              {{ email }}
            </div>
          </div>
        </div>
        <div class="account-settings-row">
          <span class="account-settings-row__label">{{ t('account.fieldAccountIdentity') }}</span>
          <span class="account-settings-row__value">
            <span class="account-settings-row__text">{{ username }}</span>
          </span>
        </div>
        <div class="account-settings-row">
          <span class="account-settings-row__label">{{ t('account.fieldEmail') }}</span>
          <span class="account-settings-row__value">
            <span class="account-settings-row__text">{{ email }}</span>
          </span>
        </div>
        <div class="account-settings-row">
          <span class="account-settings-row__label">{{ t('account.fieldRegisteredAt') }}</span>
          <span
            class="account-settings-row__value account-settings-row__value--mono"
            :class="{ 'account-settings-row__value--empty': isEmptyDisplay(registeredAt) }"
          >{{ registeredAt }}</span>
        </div>
      </section>
    </div>

    <section class="account-settings-section account-settings-section--password">
      <header class="account-settings-section__header">
        <span class="account-settings-section__icon account-settings-section__icon--password">
          <KeyRound :size="16" />
        </span>
        <h2 class="account-settings-section__title">
          {{ t('account.securitySectionPwd') }}
        </h2>
      </header>
      <ElForm
        label-position="top"
        class="account-password-form"
        @submit.prevent
      >
        <div class="account-settings-row account-settings-row--form">
          <span class="account-settings-row__label">{{ t('account.fieldCurrentPassword') }}</span>
          <div class="account-settings-row__control">
            <ElInput
              v-model="currentPassword"
              type="password"
              show-password
              autocomplete="current-password"
              :class="{ 'account-password-input--error': currentPasswordError }"
              @input="onCurrentPasswordInput"
              @blur="currentPasswordTouched = true; validateCurrentPassword(true)"
            />
            <p
              v-if="currentPasswordError"
              class="account-settings-row__error"
            >
              {{ currentPasswordError }}
            </p>
          </div>
        </div>
        <div class="account-settings-row account-settings-row--form">
          <span class="account-settings-row__label">{{ t('account.fieldNewPassword') }}</span>
          <div class="account-settings-row__control">
            <ElInput
              v-model="newPassword"
              type="password"
              show-password
              autocomplete="new-password"
              :class="{ 'account-password-input--error': newPasswordError }"
              @input="onNewPasswordInput"
              @blur="newPasswordTouched = true; validateNewPassword(true)"
            />
            <div
              v-if="newPassword"
              class="account-password-strength"
            >
              <div class="account-password-strength__bar">
                <div
                  class="account-password-strength__fill"
                  :style="{ width: `${(passwordStrength.level / 3) * 100}%`, background: passwordStrength.color }"
                />
              </div>
              <span
                class="account-password-strength__text"
                :style="{ color: passwordStrength.color }"
              >
                {{ passwordStrength.text }}
              </span>
            </div>
            <p class="account-settings-row__hint">
              {{ t('account.securityPwdHint') }}
            </p>
            <p
              v-if="newPasswordError"
              class="account-settings-row__error"
            >
              {{ newPasswordError }}
            </p>
          </div>
        </div>
        <div class="account-settings-row account-settings-row--form">
          <span class="account-settings-row__label">{{ t('account.fieldConfirmPassword') }}</span>
          <div class="account-settings-row__control">
            <ElInput
              v-model="confirmPassword"
              type="password"
              show-password
              autocomplete="new-password"
              :class="{ 'account-password-input--error': confirmPasswordError }"
              @input="onConfirmPasswordInput"
              @blur="confirmPasswordTouched = true; validateConfirmPassword(true)"
            />
            <p
              v-if="confirmPasswordError"
              class="account-settings-row__error"
            >
              {{ confirmPasswordError }}
            </p>
          </div>
        </div>
        <div class="account-settings-row account-settings-row--form account-settings-row--actions">
          <span
            class="account-settings-row__label"
            aria-hidden="true"
          />
          <div class="account-settings-row__control">
            <ElButton
              type="primary"
              :loading="passwordSubmitting"
              :disabled="passwordSubmitting"
              @click="updatePassword"
            >
              {{ t('account.btnUpdatePassword') }}
            </ElButton>
          </div>
        </div>
      </ElForm>
    </section>

    <section class="account-settings-section">
      <header class="account-settings-section__header">
        <span class="account-settings-section__icon account-settings-section__icon--audit">
          <ShieldCheck :size="16" />
        </span>
        <h2 class="account-settings-section__title">
          {{ t('account.securityAuditCardTitle') }}
        </h2>
      </header>
      <div class="account-settings-row">
        <span class="account-settings-row__label">{{ t('account.lastLoginAtLabel') }}</span>
        <span
          class="account-settings-row__value account-settings-row__value--mono"
          :class="{ 'account-settings-row__value--empty': isEmptyDisplay(lastLoginAt) }"
        >{{ lastLoginAt }}</span>
      </div>
      <div class="account-settings-row">
        <span class="account-settings-row__label">{{ t('account.lastLoginIpLabel') }}</span>
        <span
          class="account-settings-row__value account-settings-row__value--mono"
          :class="{ 'account-settings-row__value--empty': isEmptyDisplay(lastLoginIp) }"
        >{{ lastLoginIp }}</span>
      </div>
      <div class="account-settings-row">
        <span class="account-settings-row__label">{{ t('account.lastLoginLocationLabel') }}</span>
        <span
          class="account-settings-row__value"
          :class="{ 'account-settings-row__value--empty': isEmptyDisplay(lastLoginLocation) }"
        >{{ lastLoginLocation }}</span>
      </div>
    </section>
  </div>
</template>

<style scoped>
.account-settings {
  display: grid;
  gap: 18px;
  max-width: 1080px;
  width: 100%;
}

.account-settings__main {
  display: grid;
  gap: 18px;
  min-width: 0;
}

.account-settings-section {
  padding: 18px 20px 20px;
  background: var(--color-card-bg, var(--el-bg-color));
  border: 1px solid var(--color-border-light, var(--el-border-color-lighter));
  border-radius: var(--radius-card, 10px);
  box-shadow: 0 8px 22px rgb(15 23 42 / 5%);
}

.account-settings-section + .account-settings-section {
  margin-top: 0;
}

.account-settings__main .account-settings-section + .account-settings-section {
  margin-top: 0;
}

.account-settings-section--password {
  position: static;
}

.account-settings-section__header {
  display: flex;
  align-items: center;
  gap: 9px;
  padding-bottom: 13px;
  margin-bottom: 16px;
  border-bottom: 1px solid var(--color-border-light, var(--el-border-color-lighter));
}

.account-settings-section__icon {
  display: inline-flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  color: var(--color-primary, var(--el-color-primary));
  background: color-mix(in srgb, var(--color-primary, var(--el-color-primary)) 10%, transparent);
  border-radius: var(--radius-card, 10px);
}

.account-settings-section__icon--password {
  color: var(--color-success, var(--el-color-success));
  background: color-mix(in srgb, var(--color-success, var(--el-color-success)) 12%, transparent);
}

.account-settings-section__icon--audit {
  color: var(--color-warning, var(--el-color-warning));
  background: color-mix(in srgb, var(--color-warning, var(--el-color-warning)) 14%, transparent);
}

.account-settings-section__title {
  margin: 0;
  font-size: 15px;
  font-weight: 600;
  line-height: 22px;
  color: var(--color-text-title, var(--el-text-color-primary));
}

.account-overview {
  display: flex;
  align-items: center;
  gap: 14px;
  padding: 2px 0 18px;
  margin-bottom: 2px;
}

.account-overview__avatar {
  display: flex;
  flex: 0 0 auto;
  align-items: center;
  justify-content: center;
  width: 52px;
  height: 52px;
  font-size: 22px;
  font-weight: 700;
  line-height: 1;
  color: #fff;
  background: var(--color-primary, var(--el-color-primary));
  border-radius: 50%;
  box-shadow: 0 8px 18px color-mix(in srgb, var(--color-primary, var(--el-color-primary)) 24%, transparent);
}

.account-overview__content {
  min-width: 0;
}

.account-overview__name {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-size: 16px;
  line-height: 24px;
  color: var(--color-text-title, var(--el-text-color-primary));
}

.account-overview__email {
  margin-top: 3px;
  font-size: 13px;
  line-height: 20px;
  color: var(--color-text-secondary, var(--el-text-color-secondary));
  overflow-wrap: anywhere;
}

.account-settings-row {
  display: grid;
  grid-template-columns: minmax(128px, 168px) minmax(0, 1fr);
  gap: 20px;
  align-items: center;
  min-height: 42px;
}

.account-settings-row + .account-settings-row {
  margin-top: 6px;
}

.account-settings-row--form {
  align-items: flex-start;
  padding-top: 6px;
  padding-bottom: 6px;
}

.account-settings-row--actions {
  padding-top: 12px;
  padding-bottom: 0;
}

.account-settings-row--actions .account-settings-row__control {
  display: flex;
  justify-content: flex-end;
}

.account-settings-row__label {
  flex-shrink: 0;
  font-size: 13px;
  font-weight: 500;
  line-height: 34px;
  color: var(--color-text-secondary, var(--el-text-color-secondary));
}

.account-settings-row__value {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  min-width: 0;
  font-size: 14px;
  line-height: 22px;
  color: var(--color-text-title, var(--el-text-color-primary));
  overflow-wrap: anywhere;
}

.account-settings-row__value--mono {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  font-variant-numeric: tabular-nums;
}

.account-settings-row__value--empty {
  color: var(--color-text-placeholder, var(--el-text-color-placeholder));
  font-weight: 400;
}

.account-settings-row__text {
  min-width: 0;
  font-weight: 500;
  overflow-wrap: anywhere;
}

.account-role-badge {
  display: inline-flex;
  flex-shrink: 0;
  align-items: center;
  padding: 2px 10px;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.5;
  color: var(--color-primary, var(--el-color-primary));
  background: color-mix(in srgb, var(--color-primary, var(--el-color-primary)) 8%, transparent);
  border: 1px solid color-mix(in srgb, var(--color-primary, var(--el-color-primary)) 24%, transparent);
  border-radius: var(--radius-card, 10px);
}

.account-settings-row__control {
  width: 100%;
  max-width: 520px;
}

.account-settings-row__hint {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-text-secondary, var(--el-text-color-secondary));
}

.account-settings-row__error {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
  color: var(--color-error, var(--el-color-danger));
}

.account-password-input--error :deep(.el-input__wrapper) {
  box-shadow: 0 0 0 1px var(--color-error, var(--el-color-danger)) inset;
}

.account-password-strength {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
}

.account-password-strength__bar {
  flex: 1;
  height: 3px;
  overflow: hidden;
  background: var(--color-grey-3, var(--el-fill-color-light));
  border-radius: 2px;
}

.account-password-strength__fill {
  height: 100%;
  transition: width 0.3s, background 0.3s;
}

.account-password-strength__text {
  flex-shrink: 0;
  font-size: 12px;
  font-weight: 500;
  line-height: 1;
}

.account-password-form :deep(.el-input__wrapper) {
  min-height: 34px;
}

@media (max-width: 900px) {
  .account-settings-section {
    padding: 16px;
  }

  .account-settings-section + .account-settings-section {
    margin-top: 0;
  }

  .account-settings__main .account-settings-section + .account-settings-section {
    margin-top: 0;
  }
}

@media (max-width: 640px) {
  .account-settings-section__header {
    margin-bottom: 14px;
  }

  .account-overview {
    align-items: flex-start;
    padding-bottom: 16px;
  }

  .account-overview__avatar {
    width: 44px;
    height: 44px;
    font-size: 19px;
  }

  .account-settings-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
    align-items: flex-start;
    min-height: 0;
  }

  .account-settings-row + .account-settings-row {
    margin-top: 14px;
  }

  .account-settings-row--form {
    padding-top: 0;
    padding-bottom: 0;
  }

  .account-settings-row--actions {
    margin-top: 18px;
  }

  .account-settings-row--actions .account-settings-row__label {
    display: none;
  }

  .account-settings-row--actions .account-settings-row__control {
    justify-content: stretch;
  }

  .account-settings-row--actions :deep(.el-button) {
    width: 100%;
  }

  .account-settings-row__label {
    line-height: 20px;
  }

  .account-settings-row__control {
    max-width: none;
  }
}
</style>

<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Mail, Key, Lock, Eye, EyeOff, CheckCircle2 } from 'lucide-vue-next'
import { api } from '../../lib/api'
import { useTurnstileConfig } from '../../composables/useTurnstileConfig'
import AuthTurnstileField from './AuthTurnstileField.vue'

const props = withDefaults(
  defineProps<{
    initialEmail?: string
  }>(),
  {
    initialEmail: '',
  },
)

const emit = defineEmits<{
  'back-to-login': [email?: string]
  'update:step': [step: 'request' | 'reset']
}>()

const { t, locale } = useI18n()
const router = useRouter()

const {
  turnstileSiteKey,
  isTurnstilePending,
  isTurnstileReady,
  isTurnstileBlocked,
  loadTurnstileConfig,
  retryTurnstileConfig,
  buildTurnstilePayload,
  blockTurnstile,
} = useTurnstileConfig()

const view = ref<'request' | 'reset'>('request')
const turnstileToken = ref('')
const turnstileError = ref('')
const turnstileErrorCode = ref('')
const turnstileFieldRef = ref<InstanceType<typeof AuthTurnstileField> | null>(null)

const formItems = reactive({
  email: {
    value: props.initialEmail,
    errorMsg: '',
    showError: false,
  },
})

const submitLoading = ref(false)
const resetLoading = ref(false)
const resetSuccess = ref(false)
const savedEmail = ref('')
const resetCode = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const confirmPasswordTouched = ref(false)
const showNewPassword = ref(false)
const showConfirmPassword = ref(false)
const resetError = ref('')
const resendCooldown = ref(0)
const emailNotRegistered = ref(false)

let cooldownTimer: ReturnType<typeof setInterval> | null = null
let successTimer: ReturnType<typeof setTimeout> | null = null

type AuthErrorPayload = {
  message?: string
  error_code?: string
  fields?: Record<string, string[]>
}

type AuthResponse<T = undefined> = {
  code?: string
  data?: T
  error?: AuthErrorPayload
}

const regEmail = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/

const maskedEmail = computed(() => maskEmail(savedEmail.value))

const canSendResetCode = computed(() => {
  if (submitLoading.value) return false
  if (isTurnstilePending.value) return false
  if (isTurnstileBlocked.value) return false
  if (isTurnstileReady.value) return Boolean(turnstileToken.value)
  return true
})

const passwordStrength = computed(() => {
  const pwd = newPassword.value
  if (!pwd) return { level: 0, text: '' }

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

const confirmPasswordError = computed(() => {
  if (!confirmPasswordTouched.value) return ''
  if (!confirmPassword.value) return ''
  if (newPassword.value !== confirmPassword.value) return t('findPwd.passwordNotMatch')
  return ''
})

const canUpdatePassword = computed(() => {
  if (resetLoading.value || resetSuccess.value) return false
  if (!resetCode.value || resetCode.value.length !== 6) return false
  if (checkPassword(newPassword.value)) return false
  if (!confirmPassword.value) return false
  if (newPassword.value !== confirmPassword.value) return false
  return true
})

function maskEmail(email: string): string {
  const [local, domain] = email.split('@')
  if (!local || !domain) return email
  if (local.length <= 3) {
    return `${local[0] || ''}***@${domain}`
  }
  return `${local.slice(0, 3)}***@${domain}`
}

function checkMail(value: string) {
  if (!value) return t('findPwd.emailErrRequired')
  if (!regEmail.test(value)) return t('findPwd.emailErrFormat')
  return ''
}

function checkPassword(value: string) {
  if (!value) {
    return t('findPwd.passwordErrRequired')
  }
  const pattern = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d.!"@#$%&'*():;\\+/=?^_`{|}~><-]{8,20}$/
  if (!pattern.test(value)) {
    return t('findPwd.passwordFormatError')
  }
  return ''
}

function validateEmailOnInput() {
  const error = checkMail(formItems.email.value)
  if (error && formItems.email.value) {
    formItems.email.errorMsg = error
    formItems.email.showError = true
  } else {
    formItems.email.errorMsg = ''
    formItems.email.showError = false
  }
}

function clearResetError() {
  resetError.value = ''
}

function onConfirmPasswordInput() {
  confirmPasswordTouched.value = true
  clearResetError()
}

function startResendCooldown(seconds = 60) {
  resendCooldown.value = seconds
  if (cooldownTimer) clearInterval(cooldownTimer)
  cooldownTimer = setInterval(() => {
    resendCooldown.value -= 1
    if (resendCooldown.value <= 0 && cooldownTimer) {
      clearInterval(cooldownTimer)
      cooldownTimer = null
    }
  }, 1000)
}

function blockUnavailableTurnstile(errorCode = '') {
  blockTurnstile()
  turnstileToken.value = ''
  turnstileError.value = t('login.captchaUnavailable')
  turnstileErrorCode.value = errorCode
}

async function retryTurnstile() {
  turnstileToken.value = ''
  turnstileError.value = ''
  turnstileErrorCode.value = ''
  await retryTurnstileConfig()
}

function resetTurnstile() {
  if (!isTurnstileReady.value) return
  turnstileToken.value = ''
  turnstileErrorCode.value = ''
  turnstileFieldRef.value?.reset()
}

function onTurnstileSuccess(token: string) {
  turnstileToken.value = token
  turnstileError.value = ''
  turnstileErrorCode.value = ''
}

function onTurnstileExpire() {
  turnstileToken.value = ''
  turnstileError.value = t('login.captchaExpired')
  turnstileErrorCode.value = ''
}

function onTurnstileInvalidate() {
  turnstileToken.value = ''
  turnstileError.value = ''
  turnstileErrorCode.value = ''
}

function onTurnstileError(errorCode?: string) {
  blockUnavailableTurnstile(errorCode)
}

function onTurnstileLoadFailed() {
  blockUnavailableTurnstile()
}

function validateRequestForm() {
  let hasError = false

  const emailError = checkMail(formItems.email.value)
  if (emailError) {
    formItems.email.errorMsg = emailError
    formItems.email.showError = true
    hasError = true
  } else {
    formItems.email.errorMsg = ''
    formItems.email.showError = false
  }

  if (isTurnstilePending.value) {
    turnstileError.value = t('login.captchaLoading')
    hasError = true
  } else if (isTurnstileBlocked.value) {
    turnstileError.value = t('login.captchaUnavailable')
    hasError = true
  } else if (isTurnstileReady.value) {
    if (!turnstileToken.value) {
      turnstileError.value = t('findPwd.codeErrRequired')
      hasError = true
    } else {
      turnstileError.value = ''
    }
  } else {
    turnstileError.value = ''
  }

  return !hasError
}

function switchToResetView() {
  view.value = 'reset'
  emit('update:step', 'reset')
  resetCode.value = ''
  newPassword.value = ''
  confirmPassword.value = ''
  resetError.value = ''
}

function handleSendResetFieldsError(fields?: Record<string, string[]>, errorCode?: string) {
  if (!fields) return

  if (errorCode === 'EMAIL_NOT_REGISTERED') {
    emailNotRegistered.value = true
  }
  for (const [field, msgs] of Object.entries(fields)) {
    if (field === 'email') {
      formItems.email.errorMsg = msgs[0] || ''
      formItems.email.showError = true
    } else if (field === 'turnstile_token') {
      turnstileError.value = msgs[0] || ''
    }
  }
}

function resetRequestErrorMessage(errorCode?: string, fallback?: string): string {
  if (errorCode === 'EMAIL_SERVICE_UNAVAILABLE') {
    return t('findPwd.emailServiceUnavailable')
  }
  return fallback || t('findPwd.sendFailed')
}

async function sendResetCode() {
  if (submitLoading.value) return
  if (!validateRequestForm()) return

  submitLoading.value = true
  formItems.email.errorMsg = ''
  formItems.email.showError = false
  turnstileError.value = ''
  emailNotRegistered.value = false

  try {
    const postData = {
      email: formItems.email.value.trim().toLowerCase(),
      ...buildTurnstilePayload(turnstileToken.value),
    }

    const res = await api<AuthResponse<{ pending_registration?: boolean }>>('/api/v1/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify(postData),
    })

    if (res.code === '0000') {
      savedEmail.value = formItems.email.value.trim().toLowerCase()
      if (res.data?.pending_registration) {
        ElMessage.warning({ message: t('findPwd.pendingRegistration'), grouping: true })
        router.push({
          path: '/register',
          query: { email: savedEmail.value },
        })
        return
      }
      startResendCooldown()
      switchToResetView()
    } else if (res.error?.fields) {
      handleSendResetFieldsError(res.error.fields, res.error.error_code)
    } else {
      ElMessage.error({
        message: resetRequestErrorMessage(res.error?.error_code, res.error?.message),
        grouping: true,
      })
    }
  } catch (err: unknown) {
    const errObj = err as {
      message?: string
      errorCode?: string
      fields?: Record<string, string[]>
    }
    if (errObj.fields && Object.keys(errObj.fields).length > 0) {
      handleSendResetFieldsError(errObj.fields, errObj.errorCode)
    } else {
      ElMessage.error({
        message: resetRequestErrorMessage(errObj.errorCode, errObj.message),
        grouping: true,
      })
    }
  } finally {
    resetTurnstile()
    submitLoading.value = false
  }
}

function handleResend() {
  if (resendCooldown.value > 0) return
  view.value = 'request'
  emit('update:step', 'request')
  formItems.email.value = savedEmail.value
}

function goRegister() {
  router.push({
    path: '/register',
    query: formItems.email.value
      ? { email: formItems.email.value.trim().toLowerCase() }
      : undefined,
  })
}

function validateResetForm() {
  resetError.value = ''

  if (!resetCode.value || resetCode.value.length !== 6) {
    resetError.value = t('findPwd.verificationCodeErr')
    return false
  }

  const passwordError = checkPassword(newPassword.value)
  if (passwordError) {
    resetError.value = passwordError
    return false
  }

  if (newPassword.value !== confirmPassword.value) {
    resetError.value = t('findPwd.passwordNotMatch')
    return false
  }

  return true
}

async function handleUpdatePassword() {
  if (resetLoading.value || resetSuccess.value) return
  if (!validateResetForm()) return

  resetLoading.value = true
  resetError.value = ''

  try {
    const res = await api<AuthResponse>('/api/v1/auth/forgot-password/confirm', {
      method: 'POST',
      body: JSON.stringify({
        email: savedEmail.value,
        code: resetCode.value,
        password: newPassword.value,
      }),
    })

    if (res.code === '0000') {
      resetSuccess.value = true
      successTimer = setTimeout(() => {
        emit('back-to-login', savedEmail.value)
      }, 1000)
    } else {
      resetError.value = res.error?.message || t('findPwd.resetFailed')
    }
  } catch {
    resetError.value = t('findPwd.resetFailed')
  } finally {
    resetLoading.value = false
  }
}

watch(locale, () => {
  formItems.email.errorMsg = ''
  formItems.email.showError = false
  confirmPasswordTouched.value = false
  resetError.value = ''
  turnstileError.value = ''
})

onMounted(async () => {
  emit('update:step', view.value)
  await loadTurnstileConfig()
})

onUnmounted(() => {
  if (cooldownTimer) clearInterval(cooldownTimer)
  if (successTimer) clearTimeout(successTimer)
})
</script>

<template>
  <div class="reset-password-card">
    <Transition
      name="reset-view-fade"
      mode="out-in"
    >
      <!-- Request View -->
      <form
        v-if="view === 'request'"
        key="request"
        class="reset-view"
        novalidate
        @submit.prevent="sendResetCode"
      >
        <p class="reset-subtitle">
          {{ t('findPwd.requestSubtitle') }}
        </p>

        <div
          class="input-wrapper"
          :class="{ 'has-error': formItems.email.showError }"
        >
          <label
            class="sr-only"
            for="reset-request-email"
          >{{ t('findPwd.emailPlaceholder') }}</label>
          <div class="input-row">
            <Mail
              class="input-icon"
              :size="18"
              aria-hidden="true"
            />
            <input
              id="reset-request-email"
              v-model="formItems.email.value"
              type="email"
              :placeholder="t('findPwd.emailPlaceholder')"
              autocomplete="email"
              :aria-invalid="formItems.email.showError"
              :aria-describedby="formItems.email.showError ? 'reset-request-email-error' : undefined"
              @blur="validateEmailOnInput"
              @input="validateEmailOnInput(); emailNotRegistered = false"
            >
          </div>
          <p
            v-if="formItems.email.showError"
            id="reset-request-email-error"
            class="error-msg"
            role="alert"
          >
            {{ formItems.email.errorMsg }}
          </p>
          <p
            v-if="emailNotRegistered"
            class="register-hint"
          >
            <span>{{ t('findPwd.emailNotRegisteredHint') }}</span>
            <a
              href="#"
              class="register-hint-link"
              @click.prevent="goRegister"
            >{{ t('findPwd.signUp') }}</a>
          </p>
        </div>

        <AuthTurnstileField
          ref="turnstileFieldRef"
          :ready="isTurnstileReady"
          :blocked="isTurnstileBlocked"
          :verified="Boolean(turnstileToken)"
          :site-key="turnstileSiteKey"
          action="forgot_password"
          :blocked-message="t('login.captchaUnavailable')"
          :retry-label="t('login.captchaRetry')"
          :manual-retry-label="t('login.captchaManualRetry')"
          :error-code-label="turnstileErrorCode ? t('login.captchaReferenceCode', { code: turnstileErrorCode }) : ''"
          :error-message="turnstileError"
          @retry="retryTurnstile"
          @success="onTurnstileSuccess"
          @expire="onTurnstileExpire"
          @invalidate="onTurnstileInvalidate"
          @error="onTurnstileError"
          @load-failed="onTurnstileLoadFailed"
        />

        <ElButton
          type="primary"
          class="submit-btn"
          native-type="submit"
          :disabled="submitLoading || !canSendResetCode"
          :loading="submitLoading"
          @click="sendResetCode"
        >
          {{ submitLoading ? t('findPwd.btnSubmitLoading') : t('findPwd.sendResetCode') }}
        </ElButton>
      </form>

      <!-- Reset View -->
      <form
        v-else
        key="reset"
        class="reset-view"
        novalidate
        @submit.prevent="handleUpdatePassword"
      >
        <p class="reset-email-hint">
          {{ t('findPwd.codeSentTo', { email: maskedEmail }) }}
        </p>

        <div class="input-wrapper">
          <label
            class="sr-only"
            for="reset-verification-code"
          >{{ t('findPwd.digitCodePh') }}</label>
          <div class="captcha-row">
            <div class="input-row captcha-input">
              <Key
                class="input-icon"
                :size="18"
                aria-hidden="true"
              />
              <input
                id="reset-verification-code"
                v-model="resetCode"
                type="text"
                inputmode="numeric"
                pattern="[0-9]*"
                maxlength="6"
                :placeholder="t('findPwd.digitCodePh')"
                autocomplete="one-time-code"
                :aria-invalid="Boolean(resetError)"
                :aria-describedby="resetError ? 'reset-form-error' : undefined"
                @input="clearResetError"
              >
            </div>
            <button
              type="button"
              class="resend-btn"
              :disabled="resendCooldown > 0"
              @click="handleResend"
            >
              {{
                resendCooldown > 0
                  ? t('findPwd.resendIn', { seconds: resendCooldown })
                  : t('findPwd.resend')
              }}
            </button>
          </div>
        </div>

        <div class="input-wrapper">
          <label
            class="sr-only"
            for="reset-new-password"
          >{{ t('findPwd.newPasswordPlaceholder') }}</label>
          <div class="input-row">
            <Lock
              class="input-icon"
              :size="18"
              aria-hidden="true"
            />
            <input
              id="reset-new-password"
              v-model="newPassword"
              :type="showNewPassword ? 'text' : 'password'"
              :placeholder="t('findPwd.newPasswordPlaceholder')"
              autocomplete="new-password"
              :aria-invalid="Boolean(resetError)"
              :aria-describedby="resetError ? 'reset-form-error' : undefined"
              @input="clearResetError"
            >
            <button
              type="button"
              class="eye-btn"
              :aria-label="showNewPassword ? t('common.hidePassword') : t('common.showPassword')"
              :aria-pressed="showNewPassword"
              @click="showNewPassword = !showNewPassword"
            >
              <EyeOff
                v-if="showNewPassword"
                :size="16"
              />
              <Eye
                v-else
                :size="16"
              />
            </button>
          </div>
        </div>

        <div class="input-wrapper">
          <label
            class="sr-only"
            for="reset-confirm-password"
          >{{ t('findPwd.confirmPasswordPlaceholder') }}</label>
          <div class="input-row">
            <Lock
              class="input-icon"
              :size="18"
              aria-hidden="true"
            />
            <input
              id="reset-confirm-password"
              v-model="confirmPassword"
              :type="showConfirmPassword ? 'text' : 'password'"
              :placeholder="t('findPwd.confirmPasswordPlaceholder')"
              autocomplete="new-password"
              :aria-invalid="Boolean(confirmPasswordError)"
              :aria-describedby="confirmPasswordError ? 'reset-confirm-password-error' : undefined"
              @input="onConfirmPasswordInput"
              @blur="confirmPasswordTouched = true"
            >
            <button
              type="button"
              class="eye-btn"
              :aria-label="showConfirmPassword ? t('common.hidePassword') : t('common.showPassword')"
              :aria-pressed="showConfirmPassword"
              @click="showConfirmPassword = !showConfirmPassword"
            >
              <EyeOff
                v-if="showConfirmPassword"
                :size="16"
              />
              <Eye
                v-else
                :size="16"
              />
            </button>
          </div>
          <p
            v-if="confirmPasswordError"
            id="reset-confirm-password-error"
            class="error-msg"
            role="alert"
          >
            {{ confirmPasswordError }}
          </p>
        </div>

        <div
          v-if="newPassword"
          class="strength-bar-wrapper"
        >
          <div class="strength-bar">
            <div
              class="strength-fill"
              :style="{ width: (passwordStrength.level / 3 * 100) + '%', background: passwordStrength.color }"
            />
          </div>
          <span
            class="strength-text"
            :style="{ color: passwordStrength.color }"
          >{{ passwordStrength.text }}</span>
        </div>

        <p
          v-if="resetError"
          id="reset-form-error"
          class="error-msg reset-error"
          role="alert"
        >
          {{ resetError }}
        </p>

        <ElButton
          type="primary"
          class="submit-btn"
          native-type="submit"
          :class="{ 'submit-btn--success': resetSuccess }"
          :disabled="!canUpdatePassword"
          :loading="resetLoading"
          @click="handleUpdatePassword"
        >
          <span
            v-if="resetSuccess"
            class="btn-success-content"
          >
            <CheckCircle2 :size="18" />
          </span>
          <span v-else>{{ t('findPwd.updatePassword') }}</span>
        </ElButton>
      </form>
    </Transition>

    <div class="reset-footer">
      <span class="footer-text">{{ t('findPwd.alreadyHaveAccount') }}</span>
      <a
        href="#"
        class="footer-link sign-in-link"
        @click.prevent="emit('back-to-login')"
      >
        {{ t('findPwd.signIn') }}
      </a>
    </div>
  </div>
</template>

<style scoped>
.reset-password-card {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.reset-view {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.reset-subtitle,
.reset-email-hint {
  margin: 0;
  font-size: 14px;
  line-height: 1.5;
  color: rgba(228, 223, 242, 0.68);
}

.reset-email-hint {
  color: rgba(255, 255, 255, 0.65);
}

.input-wrapper {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.input-row {
  display: flex;
  align-items: center;
  height: 46px;
  padding: 0 13px;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  transition:
    background-color 150ms ease-out,
    border-color 150ms ease-out,
    box-shadow 150ms ease-out;
}

.input-row:focus-within {
  background: rgba(109, 94, 246, 0.08);
  border-color: rgba(139, 120, 255, 0.72);
  box-shadow: 0 0 0 3px rgba(109, 94, 246, 0.22);
}

.input-icon {
  color: #a19aad;
  flex-shrink: 0;
  margin-right: 10px;
}

.input-row input {
  height: 42px;
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 14px;
  font-weight: 500;
  color: #f0eefa;
  min-width: 0;
}

.input-row input::placeholder {
  color: #938c9e;
}

.eye-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  margin-left: 8px;
  color: #a19aad;
  border-radius: 7px;
  height: 36px;
  width: 36px;
  justify-content: center;
  transition: color 0.2s, background-color 0.2s;
}

.eye-btn:hover {
  color: #fff;
  background-color: rgba(255, 255, 255, 0.1);
}

.eye-btn:focus-visible,
.resend-btn:focus-visible,
.footer-link:focus-visible,
.register-hint-link:focus-visible {
  outline: 2px solid var(--color-brand-violet-soft);
  outline-offset: 2px;
}

.captcha-row {
  display: flex;
  gap: 12px;
  width: 100%;
}

.captcha-input {
  flex: 1;
}

.resend-btn {
  flex-shrink: 0;
  min-width: 72px;
  min-height: 42px;
  padding: 0 12px;
  background: transparent;
  border: 1px solid rgba(139, 92, 246, 0.4);
  border-radius: 10px;
  color: rgba(196, 181, 253, 0.85);
  font-size: 13px;
  font-weight: 400;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s, color 0.2s;
  white-space: nowrap;
}

.resend-btn:hover:not(:disabled) {
  background: rgba(139, 92, 246, 0.08);
  border-color: rgba(139, 92, 246, 0.6);
  color: #ddd6fe;
}

.resend-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.input-wrapper.has-error .input-row {
  background: rgba(255, 90, 90, 0.07);
  border-color: rgba(255, 110, 110, 0.62);
}

.error-msg {
  margin: 0;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.4;
  color: #ff8f8f;
  padding-left: 2px;
}

.register-hint {
  margin: 0;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.55);
  padding-left: 2px;
}

.register-hint-link {
  margin-left: 4px;
  color: var(--color-primary);
  text-decoration: none;
  font-weight: 500;
}

.register-hint-link:hover {
  color: #c4b5fd;
}

.reset-error {
  text-align: center;
  padding-left: 0;
}

.strength-bar-wrapper {
  display: flex;
  align-items: center;
  gap: 8px;
}

.strength-bar {
  flex: 1;
  height: 3px;
  background: #3A3B40;
  border-radius: 2px;
  overflow: hidden;
}

.strength-fill {
  height: 100%;
  transition: width 0.3s, background 0.3s;
}

.strength-text {
  font-size: 12px;
  font-weight: 500;
}

.submit-btn {
  width: 100%;
  min-height: 46px;
  height: 46px !important;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
}

.submit-btn--success {
  background: #22c55e !important;
}

.btn-success-content {
  display: flex;
  align-items: center;
  justify-content: center;
}

.reset-footer {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 4px;
  margin-top: 4px;
}

.footer-text {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.62);
}

.footer-link {
  font-size: 12px;
  text-decoration: none;
  transition: color 0.2s;
  cursor: pointer;
}

.sign-in-link {
  color: var(--color-primary);
  font-weight: 500;
}

.sign-in-link:hover {
  color: #c4b5fd;
}

.reset-view-fade-enter-active,
.reset-view-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.reset-view-fade-enter-from {
  opacity: 0;
  transform: translateX(12px);
}

.reset-view-fade-leave-to {
  opacity: 0;
  transform: translateX(-12px);
}

@media (max-width: 479.98px) {
  .eye-btn,
  .resend-btn {
    min-height: 40px;
  }
}

@media (prefers-reduced-motion: reduce) {
  .reset-view-fade-enter-active,
  .reset-view-fade-leave-active,
  .input-row,
  .strength-fill {
    transition: none;
  }
}
</style>

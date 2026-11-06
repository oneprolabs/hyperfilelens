<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Mail, Key, Lock, Eye, EyeOff, CheckCircle2 } from 'lucide-vue-next'
import { api, apiErrorMessageI18n } from '../../lib/api'
import { safeErrorDetailText } from '../../lib/errors/details'
import { useTurnstileConfig } from '../../composables/useTurnstileConfig'
import AuthTurnstileField from './AuthTurnstileField.vue'

const props = withDefaults(
  defineProps<{
    initialEmail?: string
    lockEmail?: boolean
    embedded?: boolean
    allowCancel?: boolean
    setPassword?: boolean
  }>(),
  {
    initialEmail: '',
  },
)

const emit = defineEmits<{
  'back-to-login': [email?: string]
  'update:step': [step: 'request' | 'reset']
  'password-reset': []
  cancel: []
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
const sendError = ref('')
let disposed = false

let cooldownTimer: ReturnType<typeof setInterval> | null = null
let successTimer: ReturnType<typeof setTimeout> | null = null

type AuthErrorPayload = {
  message?: string
  error_code?: string
  fields?: Record<string, string[]>
  reason?: string
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
  if (props.embedded && (resendCooldown.value > 0 || resetLoading.value || resetSuccess.value)) return false
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
  if (props.embedded && (submitLoading.value || !savedEmail.value)) return false
  if (!/^\d{6}$/.test(resetCode.value.trim())) return false
  if (checkPassword(newPassword.value)) return false
  return Boolean(confirmPassword.value) && newPassword.value === confirmPassword.value
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

function reportSendError(message: string) {
  if (props.embedded) sendError.value = safeErrorDetailText(message)
  else ElMessage.error({ message, grouping: true })
}

async function sendResetCode() {
  if (submitLoading.value) return
  if (props.embedded && (resendCooldown.value > 0 || resetLoading.value || resetSuccess.value)) return
  if (!validateRequestForm()) return

  submitLoading.value = true
  formItems.email.errorMsg = ''
  formItems.email.showError = false
  turnstileError.value = ''
  emailNotRegistered.value = false
  sendError.value = ''

  try {
    const postData = {
      email: formItems.email.value.trim().toLowerCase(),
      ...buildTurnstilePayload(turnstileToken.value),
    }

    const res = await api<AuthResponse<{ pending_registration?: boolean }>>('/api/v1/auth/forgot-password', {
      method: 'POST',
      body: JSON.stringify(postData),
    })
    if (props.embedded && disposed) return

    if (res.code === '0000') {
      if (res.data?.pending_registration) {
        if (props.embedded) {
          reportSendError(t('findPwd.pendingRegistration'))
          return
        }
        ElMessage.warning({ message: t('findPwd.pendingRegistration'), grouping: true })
        router.push({
          path: '/register',
          query: { email: formItems.email.value.trim().toLowerCase() },
        })
        return
      }
      savedEmail.value = formItems.email.value.trim().toLowerCase()
      startResendCooldown()
      if (props.embedded) resetError.value = ''
      else switchToResetView()
    } else if (res.error?.fields) {
      handleSendResetFieldsError(res.error.fields, res.error.error_code)
    } else {
      reportSendError(resetRequestErrorMessage(res.error?.error_code, res.error?.message))
    }
  } catch (err: unknown) {
    if (props.embedded && disposed) return
    const errObj = err as {
      message?: string
      errorCode?: string
      fields?: Record<string, string[]>
    }
    if (errObj.fields && Object.keys(errObj.fields).length > 0) {
      handleSendResetFieldsError(errObj.fields, errObj.errorCode)
    } else {
      reportSendError(resetRequestErrorMessage(errObj.errorCode, errObj.message))
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

  resetCode.value = resetCode.value.trim()
  if (!/^\d{6}$/.test(resetCode.value)) {
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

function passwordResetErrorMessage(error: unknown): string {
  const err = error && typeof error === 'object'
    ? error as { status?: number; errorCode?: string; detail?: { error?: AuthErrorPayload; data?: { error?: AuthErrorPayload } }; fields?: Record<string, string[]> }
    : undefined
  const payload = err?.detail?.error ?? err?.detail?.data?.error
  const errorCode = err?.errorCode ?? payload?.error_code
  if (errorCode === 'INVALID_CODE') {
    if (payload?.reason === 'EXPIRED') return t('account.passwordCodeExpired')
    if (payload?.reason === 'TOO_MANY_ATTEMPTS') return t('account.passwordCodeAttemptsExceeded')
    return t('account.passwordCodeInvalid')
  }
  if (errorCode === 'PASSWORD_RESET_RATE_LIMITED' || err?.status === 429) {
    return t('account.passwordResetRateLimited')
  }
  if (err?.status && err.status >= 500) return t('account.passwordResetServiceUnavailable')
  const fields = err?.fields ?? payload?.fields
  const fieldMessage = fields?.password?.[0] ?? fields?.code?.[0] ?? fields?.email?.[0]
  return safeErrorDetailText(fieldMessage || apiErrorMessageI18n(error, t, t('findPwd.resetFailed')))
}

async function handleUpdatePassword() {
  if (resetLoading.value || resetSuccess.value) return
  if (props.embedded && (submitLoading.value || !savedEmail.value)) return
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
    if (props.embedded && disposed) return

    if (res.code === '0000') {
      resetSuccess.value = true
      emit('password-reset')
      if (!props.embedded) {
        successTimer = setTimeout(() => {
          emit('back-to-login', savedEmail.value)
        }, 1000)
      }
    } else {
      resetError.value = passwordResetErrorMessage({
        message: res.error?.message,
        errorCode: res.error?.error_code,
        fields: res.error?.fields,
        detail: res,
      })
    }
  } catch (error: unknown) {
    if (props.embedded && disposed) return
    resetError.value = passwordResetErrorMessage(error)
  } finally {
    // Always release the request lock; form validity still controls availability.
    resetLoading.value = false
  }
}

watch(locale, () => {
  formItems.email.errorMsg = ''
  formItems.email.showError = false
  confirmPasswordTouched.value = false
  resetError.value = ''
  turnstileError.value = ''
  sendError.value = ''
})

onMounted(async () => {
  emit('update:step', view.value)
  await loadTurnstileConfig()
})

onUnmounted(() => {
  disposed = true
  if (cooldownTimer) clearInterval(cooldownTimer)
  if (successTimer) clearTimeout(successTimer)
})
</script>

<template>
  <div
    class="reset-password-card"
    :class="{ 'reset-password-card--embedded': props.embedded }"
  >
    <ElForm
      v-if="props.embedded"
      class="embedded-reset-form"
      @submit.prevent="handleUpdatePassword"
    >
      <div class="embedded-reset-row">
        <span class="embedded-reset-label">{{ t('account.fieldEmail') }}</span>
        <div class="embedded-reset-control">
          <p class="embedded-reset-email">
            {{ props.initialEmail }}
          </p>
          <p
            v-if="formItems.email.showError"
            class="embedded-reset-error"
            role="alert"
          >
            {{ formItems.email.errorMsg }}
          </p>
        </div>
      </div>
      <div class="embedded-reset-row">
        <label
          for="reset-verification-code"
          class="embedded-reset-label"
        >{{ t('account.verificationCode') }}</label>
        <div class="embedded-reset-control">
          <div class="embedded-reset-code">
            <ElInput
              id="reset-verification-code"
              v-model="resetCode"
              inputmode="numeric"
              maxlength="6"
              autocomplete="one-time-code"
              :aria-describedby="resetError ? 'reset-form-error' : undefined"
              @input="clearResetError"
            />
            <ElButton
              class="embedded-send-code"
              :disabled="!canSendResetCode"
              :loading="submitLoading"
              @click="sendResetCode"
            >
              {{ resendCooldown > 0
                ? t('account.resendCodeCountdown', { seconds: resendCooldown })
                : t(savedEmail ? 'account.resendCode' : 'account.sendCode') }}
            </ElButton>
          </div>
          <p
            v-if="sendError"
            class="embedded-reset-error"
            role="alert"
          >
            {{ sendError }}
          </p>
          <p
            v-else-if="savedEmail"
            class="embedded-reset-hint"
            role="status"
          >
            {{ t('account.codeSent') }}
          </p>
        </div>
      </div>
      <div
        v-if="isTurnstileReady || isTurnstileBlocked || turnstileError"
        class="embedded-reset-row embedded-reset-verification"
      >
        <span aria-hidden="true" />
        <div class="embedded-reset-control">
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
        </div>
      </div>
      <div class="embedded-reset-row">
        <label
          for="reset-new-password"
          class="embedded-reset-label"
        >{{ t('account.fieldNewPassword') }}</label>
        <div class="embedded-reset-control">
          <ElInput
            id="reset-new-password"
            v-model="newPassword"
            type="password"
            show-password
            autocomplete="new-password"
            @input="clearResetError"
          />
          <div
            v-if="newPassword"
            class="embedded-reset-strength"
          >
            <div class="embedded-reset-strength__bar">
              <div
                class="embedded-reset-strength__fill"
                :style="{ width: `${passwordStrength.level / 3 * 100}%`, background: passwordStrength.color }"
              />
            </div>
            <span :style="{ color: passwordStrength.color }">{{ passwordStrength.text }}</span>
          </div>
          <p class="embedded-reset-hint">
            {{ t('account.securityPwdHint') }}
          </p>
        </div>
      </div>
      <div class="embedded-reset-row">
        <label
          for="reset-confirm-password"
          class="embedded-reset-label"
        >{{ t('account.fieldConfirmPassword') }}</label>
        <div class="embedded-reset-control">
          <ElInput
            id="reset-confirm-password"
            v-model="confirmPassword"
            type="password"
            show-password
            autocomplete="new-password"
            :aria-invalid="Boolean(confirmPasswordError)"
            :aria-describedby="confirmPasswordError ? 'reset-confirm-password-error' : undefined"
            @input="onConfirmPasswordInput"
            @blur="confirmPasswordTouched = true"
          />
          <p
            v-if="confirmPasswordError"
            id="reset-confirm-password-error"
            class="embedded-reset-error"
            role="alert"
          >
            {{ confirmPasswordError }}
          </p>
          <p
            v-if="resetError"
            id="reset-form-error"
            class="embedded-reset-error"
            role="alert"
          >
            {{ resetError }}
          </p>
        </div>
      </div>
      <div class="embedded-reset-row embedded-reset-row--actions">
        <span
          class="embedded-reset-spacer"
          aria-hidden="true"
        />
        <div class="embedded-reset-control embedded-reset-actions">
          <ElButton
            v-if="props.allowCancel"
            :disabled="submitLoading || resetLoading || resetSuccess"
            @click="emit('cancel')"
          >
            {{ t('common.cancel') }}
          </ElButton>
          <ElButton
            type="primary"
            native-type="submit"
            class="embedded-confirm-password"
            :disabled="!canUpdatePassword"
            :loading="resetLoading"
            @click.prevent="handleUpdatePassword"
          >
            {{ t(props.setPassword ? 'account.setPassword' : 'account.btnUpdatePassword') }}
          </ElButton>
        </div>
      </div>
    </ElForm>
    <Transition
      v-else
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
              :readonly="props.lockEmail"
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
          <p class="reset-subtitle">
            {{ t('account.securityPwdRule') }}
          </p>
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

    <div
      v-if="!props.embedded"
      class="reset-footer"
    >
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

.reset-password-card--embedded {
  width: 100%;
  max-width: 832px;
  min-width: 0;
  color: var(--color-text-primary);
}

.embedded-reset-row {
  display: grid;
  grid-template-columns: minmax(128px, 168px) minmax(0, 1fr);
  gap: 24px;
  align-items: start;
  min-height: 42px;
  padding: 6px 0;
}

.embedded-reset-row + .embedded-reset-row {
  margin-top: 6px;
}

.embedded-reset-label {
  color: var(--color-text-secondary, var(--el-text-color-secondary));
  font-size: 14px;
  font-weight: 400;
  line-height: 34px;
}

.embedded-reset-control {
  width: 100%;
  max-width: 640px;
  min-width: 0;
}

.embedded-reset-email {
  margin: 0;
  color: var(--color-text-title, var(--el-text-color-primary));
  font-size: 14px;
  line-height: 34px;
  overflow-wrap: anywhere;
}

.embedded-reset-code {
  display: flex;
  align-items: center;
  gap: 12px;
}

.embedded-reset-code .el-input {
  min-width: 0;
}

.embedded-reset-code .el-button {
  flex-shrink: 0;
}

.embedded-reset-form :deep(.el-input__wrapper) {
  min-height: 34px;
}

.embedded-reset-hint,
.embedded-reset-error {
  margin: 6px 0 0;
  font-size: 12px;
  line-height: 1.5;
}

.embedded-reset-hint {
  color: var(--color-text-secondary, var(--el-text-color-secondary));
}

.embedded-reset-error {
  color: var(--color-error, var(--el-color-danger));
}

.embedded-reset-strength {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 8px;
  font-size: 12px;
  line-height: 1;
}

.embedded-reset-strength__bar {
  flex: 1;
  height: 3px;
  overflow: hidden;
  background: var(--color-grey-3, var(--el-fill-color-light));
  border-radius: 2px;
}

.embedded-reset-strength__fill {
  height: 100%;
}

.embedded-reset-row--actions {
  padding-top: 12px;
  padding-bottom: 0;
}

.embedded-reset-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: flex-end;
  gap: 12px;
}

.embedded-reset-actions :deep(.el-button + .el-button) {
  margin-left: 0;
}

@media (max-width: 640px) {
  .embedded-reset-row {
    grid-template-columns: minmax(0, 1fr);
    gap: 4px;
    min-height: 0;
    padding: 0;
  }

  .embedded-reset-row + .embedded-reset-row {
    margin-top: 14px;
  }

  .embedded-reset-label {
    line-height: 20px;
  }

  .embedded-reset-spacer,
  .embedded-reset-verification > span {
    display: none;
  }

  .embedded-reset-row--actions {
    margin-top: 18px;
  }

  .embedded-reset-actions {
    flex-direction: column;
    align-items: stretch;
  }
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

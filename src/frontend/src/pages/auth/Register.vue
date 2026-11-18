<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import {
  Mail,
  Lock,
  Key,
  Eye,
  EyeOff,
  CheckCircle2,
  UserRound,
  Building2,
} from 'lucide-vue-next'
import { api } from '../../lib/api'
import { unwrapApiPayload } from '../../lib/parse'
import { useTurnstileConfig } from '../../composables/useTurnstileConfig'
import { fetchDeployProfile } from '../../composables/useDeployProfile'
import AuthBackdrop from '../../components/auth/AuthBackdrop.vue'
import AuthBrandPanel from '../../components/auth/AuthBrandPanel.vue'
import AuthTurnstileField from '../../components/auth/AuthTurnstileField.vue'
import LanguageSwitcher from '../../components/LanguageSwitcher.vue'
import { appConfig } from '../../lib/appConfig'
import { trackAppEvent } from '../../lib/analytics'
import { LOGIN_ROUTE_NAME } from '../../lib/loginNavigation'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const showEula = appConfig.showEula

const {
  turnstileSiteKey,
  isTurnstilePending,
  isTurnstileReady,
  isTurnstileBlocked,
  authTurnstileMountGeneration,
  loadTurnstileConfig,
  retryTurnstileConfig,
  buildTurnstilePayload,
  blockTurnstile,
} = useTurnstileConfig()

const turnstileToken = ref('')
const turnstileError = ref('')
const turnstileErrorCode = ref('')
const turnstileFieldRef = ref<InstanceType<typeof AuthTurnstileField> | null>(null)

const formItems = reactive({
  email: {
    value: typeof route.query.email === 'string' ? route.query.email.trim().toLowerCase() : '',
    errorMsg: '',
    showError: false,
  },
  code: {
    value: '',
    errorMsg: '',
    showError: false,
  },
  password: {
    value: '',
    errorMsg: '',
    showError: false,
  },
})

const submitLoading = ref(false)
const sendCodeLoading = ref(false)
const sendCodeCooldown = ref(0)
const agree = ref(false)
const shakeTerms = ref(false)
const registerSuccess = ref(false)
const showPassword = ref(false)
const registrationMode = ref<'personal' | 'enterprise'>('personal')
const organizationName = ref('')
const organizationNameError = ref('')
const registrationOptions = ref<{
  personal: { enabled: boolean; available: boolean; reason?: string | null }
  enterprise: { enabled: boolean }
} | null>(null)
const enterpriseRegistrationEnabled = computed(
  () => registrationOptions.value?.enterprise.enabled === true,
)
const personalRegistrationAvailable = computed(
  () => {
    const options = registrationOptions.value
    return options?.personal.enabled === true && options.personal.available === true
  },
)
const hasRegistrationChoice = computed(
  () => personalRegistrationAvailable.value && enterpriseRegistrationEnabled.value,
)

let cooldownTimer: ReturnType<typeof setInterval> | null = null

const regEmail = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/
const SEND_CODE_COOLDOWN_SECONDS = 60
const SEND_CODE_COOLDOWN_KEY = 'hfl:register:send-code-cooldown-until'

type AuthErrorPayload = {
  message?: string
  fields?: Record<string, string[]>
}

type AuthResponse<T = undefined> = {
  code?: string
  data?: T
  error?: AuthErrorPayload
}

function checkMail(value: string) {
  if (!value) return t('register.emailErrRequired')
  if (!regEmail.test(value)) return t('register.emailErrFormat')
  return ''
}

function checkPassword(value: string) {
  if (!value) {
    return t('register.passwordErrRequired')
  }
  const pattern = /^(?=.*[a-z])(?=.*[A-Z])(?=.*\d)[A-Za-z\d.!"@#$%&'*():;\\+/=?^_`{|}~><-]{8,20}$/
  if (!pattern.test(value)) {
    return t('register.passwordErrFormat')
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

function validatePasswordOnInput() {
  const error = checkPassword(formItems.password.value)
  if (error && formItems.password.value) {
    formItems.password.errorMsg = error
    formItems.password.showError = true
  } else {
    formItems.password.errorMsg = ''
    formItems.password.showError = false
  }
}

const passwordStrength = computed(() => {
  const pwd = formItems.password.value
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

function clearStoredCooldown() {
  localStorage.removeItem(SEND_CODE_COOLDOWN_KEY)
}

function remainingCooldownSeconds() {
  const raw = localStorage.getItem(SEND_CODE_COOLDOWN_KEY)
  const cooldownUntil = raw ? Number(raw) : 0
  if (!Number.isFinite(cooldownUntil) || cooldownUntil <= Date.now()) {
    clearStoredCooldown()
    return 0
  }
  return Math.ceil((cooldownUntil - Date.now()) / 1000)
}

function stopCooldownTimer() {
  if (cooldownTimer) clearInterval(cooldownTimer)
  cooldownTimer = null
}

function startCooldown(seconds = SEND_CODE_COOLDOWN_SECONDS, persist = true) {
  stopCooldownTimer()
  if (persist) {
    localStorage.setItem(SEND_CODE_COOLDOWN_KEY, String(Date.now() + seconds * 1000))
  }

  sendCodeCooldown.value = seconds
  cooldownTimer = setInterval(() => {
    const remaining = remainingCooldownSeconds()
    sendCodeCooldown.value = remaining
    if (remaining <= 0) {
      stopCooldownTimer()
    }
  }, 1000)
}

function restoreCooldown() {
  const remaining = remainingCooldownSeconds()
  if (remaining > 0) {
    startCooldown(remaining, false)
  }
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

function clearCodeError() {
  formItems.code.errorMsg = ''
  formItems.code.showError = false
}

function selectRegistrationMode(mode: 'personal' | 'enterprise') {
  if (mode === 'personal' && !personalRegistrationAvailable.value) return
  if (mode === 'enterprise' && !enterpriseRegistrationEnabled.value) return
  registrationMode.value = mode
  if (mode === 'personal') {
    organizationName.value = ''
    organizationNameError.value = ''
  }
}

function handleRegistrationModeKeydown(event: KeyboardEvent) {
  if (!hasRegistrationChoice.value) return
  if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return
  event.preventDefault()
  const mode = event.key === 'Home' ? 'personal'
    : event.key === 'End' ? 'enterprise'
      : registrationMode.value === 'personal' ? 'enterprise' : 'personal'
  selectRegistrationMode(mode)
  const button = event.currentTarget as HTMLButtonElement
  button.parentElement?.querySelector<HTMLButtonElement>(`[data-mode="${mode}"]`)?.focus()
}

function applyFieldError(field: keyof typeof formItems, message: string) {
  formItems[field].errorMsg = message
  formItems[field].showError = Boolean(message)
}

function applySendCodeFieldsError(fields?: Record<string, string[]>) {
  if (!fields) return false

  let handled = false
  if (fields.turnstile_token) {
    turnstileError.value = fields.turnstile_token[0] || t('login.captchaInvalid')
    handled = true
  }
  if (fields.email) {
    applyFieldError('email', fields.email[0] || '')
    handled = true
  }
  return handled
}

function applyRegisterFieldsError(fields?: Record<string, string[]>) {
  if (!fields) return false

  let handled = false
  for (const [field, msgs] of Object.entries(fields)) {
    if (field === 'organization_name') {
      organizationNameError.value = msgs[0] || ''
      handled = true
      continue
    }
    if (field in formItems) {
      applyFieldError(field as keyof typeof formItems, msgs[0] || '')
      handled = true
    }
  }
  return handled
}

function validateSendCodeForm() {
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
      turnstileError.value = t('login.captchaErrRequired')
      hasError = true
    } else {
      turnstileError.value = ''
    }
  } else {
    turnstileError.value = ''
  }

  return !hasError
}

async function handleSendCode() {
  if (sendCodeLoading.value || sendCodeCooldown.value > 0) return
  if (!validateSendCodeForm()) return

  sendCodeLoading.value = true
  formItems.email.errorMsg = ''
  formItems.email.showError = false
  turnstileError.value = ''

  try {
    const res = await api<AuthResponse>('/api/v1/auth/email-register/send-code', {
      method: 'POST',
      body: JSON.stringify({
        email: formItems.email.value.trim().toLowerCase(),
        registration_mode: registrationMode.value,
        ...buildTurnstilePayload(turnstileToken.value),
      }),
    })

    if (res.code === '0000') {
      ElMessage.success({ message: t('register.sendCodeSuccess'), grouping: true })
      startCooldown()
    } else if (res.error?.fields) {
      applySendCodeFieldsError(res.error.fields)
    } else {
      ElMessage.error({ message: res.error?.message || t('register.sendCodeFailed'), grouping: true })
    }
  } catch (err: unknown) {
    const errObj = err as { message?: string; fields?: Record<string, string[]> }
    if (applySendCodeFieldsError(errObj.fields)) return
    ElMessage.error({ message: errObj.message || t('register.sendCodeFailed'), grouping: true })
  } finally {
    resetTurnstile()
    sendCodeLoading.value = false
  }
}

function validateForm() {
  let hasError = false

  if (
    registrationMode.value === 'personal'
    && !personalRegistrationAvailable.value
  ) {
    ElMessage.error({
      message: t('register.personalUnavailable'),
      grouping: true,
    })
    hasError = true
  }

  if (registrationMode.value === 'enterprise' && !enterpriseRegistrationEnabled.value) {
    ElMessage.error({ message: t('register.enterpriseUnavailable'), grouping: true })
    hasError = true
  }

  const emailError = checkMail(formItems.email.value)
  if (emailError) {
    formItems.email.errorMsg = emailError
    formItems.email.showError = true
    hasError = true
  }

  if (!formItems.code.value) {
    formItems.code.errorMsg = t('register.codeErrRequired')
    formItems.code.showError = true
    hasError = true
  }

  if (registrationMode.value === 'enterprise' && !organizationName.value.trim()) {
    ElMessage.warning({
      message: t('register.organizationRequired'),
      grouping: true,
    })
    hasError = true
  }

  const passwordError = checkPassword(formItems.password.value)
  if (passwordError) {
    formItems.password.errorMsg = passwordError
    formItems.password.showError = true
    hasError = true
  }

  return !hasError
}

async function handleSubmit() {
  if (submitLoading.value) return
  if (!validateForm()) return

  if (showEula && !agree.value) {
    shakeTerms.value = false
    setTimeout(() => { shakeTerms.value = true }, 10)
    ElMessage.warning({ message: t('register.agreeRequired'), grouping: true })
    return
  }

  submitLoading.value = true
  formItems.code.errorMsg = ''
  formItems.code.showError = false
  formItems.password.errorMsg = ''
  formItems.password.showError = false

  try {
    const res = await api<AuthResponse>('/api/v1/auth/email-register/confirm', {
      method: 'POST',
      body: JSON.stringify({
        email: formItems.email.value.trim().toLowerCase(),
        code: formItems.code.value,
        password: formItems.password.value,
        registration_mode: registrationMode.value,
        organization_name: organizationName.value.trim() || undefined,
        eula_accepted: showEula ? agree.value : true,
      }),
    })

    if (res.code === '0000') {
      trackAppEvent('sign_up', { method: 'email' })
      registerSuccess.value = true
    } else if (res.error?.fields) {
      applyRegisterFieldsError(res.error.fields as Record<string, string[]>)
      if (res.error?.message && !res.error?.fields) {
        ElMessage.error({ message: res.error.message, grouping: true })
      }
    } else {
      ElMessage.error({ message: res.error?.message || t('register.registerFailed'), grouping: true })
    }
  } catch (err: unknown) {
    const errObj = err as { message?: string; fields?: Record<string, string[]> }
    if (applyRegisterFieldsError(errObj.fields)) return
    ElMessage.error({ message: errObj.message || t('register.registerFailed'), grouping: true })
  } finally {
    submitLoading.value = false
  }
}

function goLogin() {
  const email = formItems.email.value.trim().toLowerCase()
  router.push(email ? { path: '/login', query: { email } } : '/login')
}

onMounted(async () => {
  const profile = await fetchDeployProfile()
  if (!profile?.email_signup_enabled) {
    await router.replace({ name: LOGIN_ROUTE_NAME })
    return
  }
  if (profile.email_signup_enabled) {
    try {
      const options = unwrapApiPayload<{
        personal: { enabled: boolean; available: boolean; reason?: string | null }
        enterprise: { enabled: boolean }
      }>(await api<unknown>('/api/v1/meta/registration-options'))
      registrationOptions.value = options
      if (options.personal.enabled && options.personal.available) {
        registrationMode.value = 'personal'
      } else if (options.enterprise.enabled) {
        registrationMode.value = 'enterprise'
      } else {
        await router.replace({ name: LOGIN_ROUTE_NAME })
        return
      }
    } catch {
      registrationOptions.value = {
        personal: { enabled: false, available: false, reason: 'unavailable' },
        enterprise: { enabled: false },
      }
      await router.replace({ name: LOGIN_ROUTE_NAME })
      return
    }
  }
  trackAppEvent('sign_up_started', { method: 'email' })
  turnstileToken.value = ''
  restoreCooldown()
  await loadTurnstileConfig()
})

onUnmounted(() => {
  stopCooldownTimer()
})
</script>

<template>
  <div class="register-container">
    <AuthBackdrop />

    <div class="left-logo">
      <div class="flex flex-col items-start w-full">
        <AuthBrandPanel
          :description="t('login.brandDesc')"
          :slogan="t('login.brandSlogan')"
        />
      </div>
    </div>

    <div class="register-form-box">
      <div class="register-box-title">
        <span class="register-box-title__copy">{{ t('register.welcomeTitle') }}</span>
        <LanguageSwitcher variant="auth" />
      </div>

      <div class="register-box-content">
        <div
          v-if="hasRegistrationChoice"
          class="registration-picker"
          role="radiogroup"
          :aria-label="t('register.accountType')"
          aria-describedby="registration-description"
        >
          <button
            type="button"
            class="registration-option"
            :class="{ 'is-selected': registrationMode === 'personal' }"
            data-mode="personal"
            role="radio"
            :aria-checked="registrationMode === 'personal'"
            :tabindex="registrationMode === 'personal' ? 0 : -1"
            @click="selectRegistrationMode('personal')"
            @keydown="handleRegistrationModeKeydown"
          >
            <UserRound
              :size="16"
              aria-hidden="true"
            />
            {{ t('register.personal') }}
          </button>
          <button
            type="button"
            class="registration-option"
            :class="{ 'is-selected': registrationMode === 'enterprise' }"
            data-mode="enterprise"
            role="radio"
            :aria-checked="registrationMode === 'enterprise'"
            :tabindex="registrationMode === 'enterprise' ? 0 : -1"
            @click="selectRegistrationMode('enterprise')"
            @keydown="handleRegistrationModeKeydown"
          >
            <Building2
              :size="16"
              aria-hidden="true"
            />
            {{ t('register.enterprise') }}
          </button>
        </div>

        <p
          v-if="hasRegistrationChoice"
          id="registration-description"
          class="registration-description"
          aria-live="polite"
        >
          {{ t(registrationMode === 'personal'
            ? 'register.personalDescription'
            : 'register.enterpriseDescription') }}
        </p>

        <div
          v-if="registrationMode === 'enterprise'"
          class="input-wrapper"
          :class="{ 'has-error': organizationNameError }"
        >
          <div class="input-row">
            <Building2
              class="input-icon"
              :size="18"
              aria-hidden="true"
            />
            <input
              v-model="organizationName"
              type="text"
              :placeholder="t('register.organizationPh')"
              :aria-label="t('register.organizationPh')"
              :aria-invalid="Boolean(organizationNameError)"
              maxlength="200"
              autocomplete="organization"
              @input="organizationNameError = ''"
            >
          </div>
          <p
            v-if="organizationNameError"
            class="error-msg"
          >
            {{ organizationNameError }}
          </p>
        </div>

        <!-- Email -->
        <div
          class="input-wrapper"
          :class="{ 'has-error': formItems.email.showError }"
        >
          <div class="input-row">
            <Mail
              class="input-icon"
              :size="18"
              aria-hidden="true"
            />
            <input
              v-model="formItems.email.value"
              type="email"
              :placeholder="t('register.emailPh')"
              :aria-label="t('register.emailPh')"
              :aria-invalid="formItems.email.showError"
              autocomplete="email"
              @blur="validateEmailOnInput"
              @input="validateEmailOnInput"
            >
          </div>
          <p
            v-if="formItems.email.showError"
            class="error-msg"
          >
            {{ formItems.email.errorMsg }}
          </p>
        </div>

        <AuthTurnstileField
          :key="authTurnstileMountGeneration"
          ref="turnstileFieldRef"
          :ready="isTurnstileReady"
          :blocked="isTurnstileBlocked"
          :verified="Boolean(turnstileToken)"
          :site-key="turnstileSiteKey"
          action="register_send_code"
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

        <!-- Email Verification Code -->
        <div
          class="input-wrapper"
          :class="{ 'has-error': formItems.code.showError }"
        >
          <div class="input-row captcha-row">
            <Key
              class="input-icon"
              :size="18"
              aria-hidden="true"
            />
            <input
              v-model="formItems.code.value"
              type="text"
              :placeholder="t('register.codePlaceholder')"
              :aria-label="t('register.codePlaceholder')"
              :aria-invalid="formItems.code.showError"
              maxlength="6"
              inputmode="numeric"
              autocomplete="one-time-code"
              @input="clearCodeError"
            >
            <button
              type="button"
              class="send-code-btn"
              :class="{ 'is-loading': sendCodeLoading }"
              :disabled="sendCodeLoading || sendCodeCooldown > 0"
              :aria-busy="sendCodeLoading"
              @click="handleSendCode"
            >
              {{
                sendCodeLoading
                  ? t('register.sendCodeLoading')
                  : sendCodeCooldown > 0
                    ? t('register.sendCodeCountdown', { n: sendCodeCooldown })
                    : t('register.sendCodeBtn')
              }}
            </button>
          </div>
          <p
            v-if="formItems.code.showError"
            class="error-msg"
          >
            {{ formItems.code.errorMsg }}
          </p>
        </div>

        <!-- Password -->
        <div
          class="input-wrapper"
          :class="{ 'has-error': formItems.password.showError }"
        >
          <div class="input-row">
            <Lock
              class="input-icon"
              :size="18"
              aria-hidden="true"
            />
            <input
              v-model="formItems.password.value"
              :type="showPassword ? 'text' : 'password'"
              :placeholder="t('register.passwordPh')"
              :aria-label="t('register.passwordPh')"
              :aria-invalid="formItems.password.showError"
              autocomplete="new-password"
              @blur="validatePasswordOnInput"
              @input="validatePasswordOnInput"
              @keyup.enter="handleSubmit"
            >
            <button
              type="button"
              class="eye-btn"
              :aria-label="showPassword ? t('common.hidePassword') : t('common.showPassword')"
              :aria-pressed="showPassword"
              @click="showPassword = !showPassword"
            >
              <EyeOff
                v-if="showPassword"
                class="eye-icon"
                :size="17"
                aria-hidden="true"
              />
              <Eye
                v-else
                class="eye-icon"
                :size="17"
                aria-hidden="true"
              />
            </button>
          </div>
          <div
            v-if="formItems.password.value"
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
            v-if="formItems.password.showError"
            class="error-msg"
          >
            {{ formItems.password.errorMsg }}
          </p>
        </div>

        <!-- Agreement -->
        <div
          v-if="showEula"
          :class="{ shake: shakeTerms }"
          class="agree-terms"
          @animationend="shakeTerms = false"
        >
          <ElCheckbox
            v-model="agree"
            class="agree-checkbox"
          >
            <span class="footer-text">{{ t('register.agree') }}</span>
            <a
              class="footer-link"
              href="https://oneprocloud.com/eula"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ $t('login.eulaNoticeLinkText') }}
            </a>{{ t('login.eulaSuffix') }}
          </ElCheckbox>
        </div>

        <!-- Submit Button -->
        <ElButton
          type="primary"
          class="submit-btn"
          :disabled="submitLoading"
          :loading="submitLoading"
          @click="handleSubmit"
        >
          {{
            submitLoading
              ? t('register.btnSubmitLoading')
              : registrationMode === 'personal'
                ? t('register.createPersonalAccount')
                : t('register.createOrganization')
          }}
        </ElButton>

        <!-- Register Footer -->
        <div class="register-footer">
          <div class="footer-row">
            <span class="footer-text">{{ t('register.hasAccount') }}</span>
            <a
              href="#"
              class="footer-link sign-in-link"
              @click.prevent="goLogin"
            >{{ t('register.backToLogin') }}</a>
          </div>
        </div>
      </div>
    </div>

    <!-- Success Overlay -->
    <Transition name="auth-success-fade">
      <div
        v-if="registerSuccess"
        class="register-success-overlay"
        @click.self="goLogin()"
      >
        <div
          class="register-success-card"
          role="dialog"
          aria-labelledby="register-success-title"
        >
          <div class="register-success-icon">
            <CheckCircle2
              :size="40"
              stroke-width="1.75"
            />
          </div>
          <h2
            id="register-success-title"
            class="register-success-title"
          >
            {{ t('register.registerSuccessTitle') }}
          </h2>
          <p class="register-success-desc">
            {{ t('register.registerSuccessDesc') }}
          </p>
          <div class="register-success-email">
            <Mail :size="16" />
            <span>{{ formItems.email.value }}</span>
          </div>
          <p class="register-success-next">
            {{ t('register.registerSuccessNext') }}
          </p>
          <button
            type="button"
            class="register-success-btn btn-primary"
            @click="goLogin()"
          >
            {{ t('register.registerSuccessLogin') }}
          </button>
        </div>
      </div>
    </Transition>
  </div>
</template>

<style scoped>
.register-container {
  box-sizing: border-box;
  width: 100%;
  min-height: var(--app-viewport-height);
  padding: 64px max(48px, calc((100vw - 1440px) / 2));
  background-color: #08090C;
  display: grid;
  grid-template-columns: minmax(0, 3fr) minmax(420px, 2fr);
  align-items: center;
  overflow-x: hidden;
  overflow-y: auto;
  position: relative;
}

.left-logo {
  width: min(540px, 100%);
  min-width: 0;
  margin: 0;
  justify-self: center;
  z-index: 10;
  display: flex;
  align-items: center;
}

.register-form-box {
  box-sizing: border-box;
  min-width: 420px;
  width: 420px;
  justify-self: center;
  padding: 30px;
  background: linear-gradient(180deg, rgba(30, 26, 40, 0.92), rgba(20, 17, 28, 0.94));
  border-radius: 18px;
  box-shadow:
    0 28px 70px rgba(0, 0, 0, 0.48),
    inset 0 1px 0 rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.09);
  backdrop-filter: blur(20px);
  z-index: 10;
}

.register-box-title {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  font-weight: 700;
  color: #FFF;
}

.register-box-title__copy {
  min-width: 0;
  font-size: 17.5px;
  line-height: 1.35;
  overflow-wrap: normal;
}

.register-box-content {
  margin-top: 22px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.registration-picker {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-bottom: 1px solid rgba(255, 255, 255, 0.1);
  margin-bottom: 4px;
}

.registration-option {
  position: relative;
  min-width: 0;
  min-height: 44px;
  padding: 0 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  border: 0;
  background: transparent;
  color: rgba(228, 223, 242, 0.58);
  font-size: 14px;
  font-weight: 500;
  line-height: 20px;
  cursor: pointer;
  transition: color 150ms ease-out;
}

.registration-option::after {
  position: absolute;
  right: 20px;
  bottom: -1px;
  left: 20px;
  height: 2px;
  content: '';
  background: var(--color-brand-violet-soft);
  border-radius: 999px;
  transform: scaleX(0);
  transform-origin: center;
  transition: transform 150ms ease-out;
}

.registration-option:hover {
  color: rgba(255, 255, 255, 0.84);
}

.registration-option.is-selected {
  color: #fff;
  font-weight: 600;
}

.registration-option.is-selected::after {
  transform: scaleX(1);
}

.registration-option:focus-visible {
  outline: 2px solid var(--color-brand-violet-soft);
  outline-offset: 2px;
}

.registration-description {
  margin: 0 0 4px;
  color: rgba(255, 255, 255, 0.72);
  font-size: 12px;
  line-height: 1.6;
}

.input-wrapper {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.input-row {
  display: flex;
  align-items: center;
  background: rgba(255, 255, 255, 0.05);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 10px;
  height: 46px;
  padding: 0 13px;
  transition:
    background-color 150ms ease-out,
    border-color 150ms ease-out,
    box-shadow 150ms ease-out;
  min-width: 0;
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
  justify-content: center;
  margin-left: 8px;
  color: #a19aad;
  border-radius: 7px;
  width: 36px;
  height: 36px;
  flex-shrink: 0;
  transition: color 0.2s, background-color 0.2s;
}

.eye-btn:hover {
  color: #fff;
  background-color: rgba(255, 255, 255, 0.1);
}

.eye-btn:focus-visible,
.send-code-btn:focus-visible,
.footer-link:focus-visible {
  outline: 2px solid var(--color-brand-violet-soft);
  outline-offset: 2px;
}

.captcha-row {
  width: 100%;
}

.send-code-btn {
  flex: 0 0 auto;
  min-width: 96px;
  min-height: 42px;
  height: 42px;
  padding: 0 8px 0 12px;
  background: transparent;
  border: 0;
  border-left: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 0;
  color: var(--color-brand-violet-soft);
  font-size: 12px;
  font-weight: 500;
  cursor: pointer;
  white-space: nowrap;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
}

.send-code-btn.is-loading::before {
  content: '';
  width: 12px;
  height: 12px;
  border: 2px solid rgba(196, 181, 253, 0.3);
  border-top-color: rgba(196, 181, 253, 0.9);
  border-radius: 50%;
  animation: send-code-spin 0.8s linear infinite;
}

@keyframes send-code-spin {
  to {
    transform: rotate(360deg);
  }
}

.send-code-btn:hover:not(:disabled) {
  color: #fff;
}

.send-code-btn:active:not(:disabled) {
  background: rgba(109, 40, 217, 0.14);
}

.send-code-btn:disabled {
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

.agree-terms {
  margin-top: 4px;
}

.agree-checkbox {
  height: auto;
  align-items: center;
}

.agree-checkbox :deep(.el-checkbox__label) {
  display: inline-flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
  padding-left: 8px;
  line-height: 1.4;
  white-space: normal;
}

.agree-checkbox :deep(.el-checkbox__inner) {
  background-color: transparent;
  border-color: rgba(255, 255, 255, 0.35);
}

.agree-checkbox :deep(.el-checkbox__input.is-checked .el-checkbox__inner) {
  background-color: var(--color-primary);
  border-color: var(--color-primary);
}

.agree-checkbox :deep(.el-checkbox__input.is-focus .el-checkbox__inner) {
  border-color: var(--color-primary);
}

.register-footer {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  padding-top: 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.footer-row {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-wrap: wrap;
  gap: 4px;
}

.footer-text {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.62);
}

.footer-link {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.7);
  text-decoration: underline;
  transition: color 0.2s;
  cursor: pointer;
}

.footer-link:hover {
  color: #fff;
}

.sign-in-link {
  color: var(--color-primary);
  font-weight: 500;
}

.sign-in-link:hover {
  color: #c4b5fd;
}

@keyframes shake {
  0% { transform: translateX(0); }
  20% { transform: translateX(-8px); }
  40% { transform: translateX(8px); }
  60% { transform: translateX(-8px); }
  80% { transform: translateX(8px); }
  100% { transform: translateX(0); }
}

.shake {
  animation: shake 0.4s;
}

.submit-btn {
  width: 100%;
  min-height: 46px;
  height: 46px !important;
  border-radius: 10px;
  font-size: 14px;
  font-weight: 600;
}

.register-success-overlay {
  position: fixed;
  inset: 0;
  z-index: 2000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 24px;
  background: rgba(8, 9, 12, 0.72);
  backdrop-filter: blur(6px);
}

.register-success-card {
  width: 100%;
  max-width: 400px;
  padding: 36px 32px 28px;
  text-align: center;
  background: hsla(0, 0%, 100%, 0.1);
  border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: var(--radius-card);
  box-shadow: 0 24px 48px rgba(0, 0, 0, 0.45);
}

.register-success-icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 72px;
  height: 72px;
  margin: 0 auto 20px;
  border-radius: 50%;
  background: rgba(103, 194, 58, 0.12);
  color: #67c23a;
  box-shadow: 0 0 32px rgba(103, 194, 58, 0.2);
}

.register-success-title {
  margin: 0 0 8px;
  font-size: 22px;
  font-weight: 600;
  color: #fff;
  line-height: 1.3;
}

.register-success-desc {
  margin: 0 0 20px;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.55);
  line-height: 1.5;
}

.register-success-email {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  max-width: 100%;
  padding: 8px 16px;
  margin-bottom: 16px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 20px;
  font-size: 14px;
  color: rgba(255, 255, 255, 0.85);
}

.register-success-email span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.register-success-email svg {
  flex-shrink: 0;
  color: #888a8f;
}

.register-success-next {
  margin: 0 0 24px;
  font-size: 13px;
  color: rgba(255, 255, 255, 0.45);
}

.register-success-btn {
  width: 100%;
  height: 40px;
  border-radius: 21px;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
}

.auth-success-fade-enter-active,
.auth-success-fade-leave-active {
  transition: opacity 0.25s ease;
}

.auth-success-fade-enter-active .register-success-card,
.auth-success-fade-leave-active .register-success-card {
  transition: transform 0.25s ease, opacity 0.25s ease;
}

.auth-success-fade-enter-from,
.auth-success-fade-leave-to {
  opacity: 0;
}

.auth-success-fade-enter-from .register-success-card,
.auth-success-fade-leave-to .register-success-card {
  transform: translateY(12px) scale(0.97);
  opacity: 0;
}

@media (max-width: 1279.98px) {
  .register-container {
    min-height: var(--app-viewport-height);
    height: auto;
    display: flex;
    flex-direction: column;
    justify-content: flex-start;
    gap: 18px;
    box-sizing: border-box;
    padding: calc(24px + var(--app-safe-top)) max(20px, var(--app-safe-right)) calc(24px + var(--app-safe-bottom)) max(20px, var(--app-safe-left));
  }

  .left-logo {
    width: auto;
    min-width: 0;
    margin: 0;
  }

  .register-form-box {
    width: min(420px, 100%);
    min-width: 0;
    padding: 32px 28px;
  }
}

@media (max-width: 479.98px) {
  .register-container {
    gap: 14px;
    padding: calc(14px + var(--app-safe-top)) max(12px, var(--app-safe-right)) calc(16px + var(--app-safe-bottom)) max(12px, var(--app-safe-left));
  }

  .register-form-box {
    padding: 24px 18px;
  }

  .register-box-title__copy {
    font-size: 20px;
  }

  .register-box-content {
    margin-top: 20px;
  }

  .captcha-row {
    width: 100%;
  }

  .send-code-btn {
    min-width: 104px;
  }

  .input-row,
  .send-code-btn,
  .submit-btn,
  .register-success-btn {
    min-height: 44px;
  }

  .eye-btn {
    min-width: 40px;
    min-height: 44px;
  }

  .register-success-overlay {
    align-items: flex-start;
    overflow-y: auto;
    padding: calc(16px + var(--app-safe-top)) max(12px, var(--app-safe-right)) calc(16px + var(--app-safe-bottom)) max(12px, var(--app-safe-left));
  }

  .register-success-card {
    box-sizing: border-box;
    padding: 28px 18px 22px;
  }
}

@media (max-height: 720px) and (min-width: 1280px) {
  .register-container {
    align-items: flex-start;
    padding-top: 40px;
    padding-bottom: 40px;
  }
}

@media (max-width: 479.98px) and (min-height: 720px) {
  .register-container {
    padding-top: calc(clamp(32px, 6dvh, 48px) + var(--app-safe-top));
  }
}

@media (prefers-reduced-motion: reduce) {
  .registration-option,
  .registration-option::after,
  .input-row {
    transition: none;
  }
}
</style>

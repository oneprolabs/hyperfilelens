<script setup lang="ts">
import { ref, reactive, computed, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { Mail, Lock, Key, Eye, EyeOff, CheckCircle2 } from 'lucide-vue-next'
import { api } from '../../lib/api'
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
        <!-- Email -->
        <div
          class="input-wrapper"
          :class="{ 'has-error': formItems.email.showError }"
        >
          <div class="input-row">
            <Mail
              class="input-icon"
              :size="18"
            />
            <input
              v-model="formItems.email.value"
              type="text"
              :placeholder="t('register.emailPh')"
              tabindex="1"
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
          <div class="captcha-row">
            <div class="input-row captcha-input">
              <Key
                class="input-icon"
                :size="18"
              />
              <input
                v-model="formItems.code.value"
                type="text"
                :placeholder="t('register.codePlaceholder')"
                maxlength="6"
                tabindex="3"
                @input="clearCodeError"
              >
            </div>
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
            />
            <input
              v-model="formItems.password.value"
              :type="showPassword ? 'text' : 'password'"
              :placeholder="t('register.passwordPh')"
              tabindex="4"
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
                :size="16"
              />
              <Eye
                v-else
                class="eye-icon"
                :size="16"
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
          {{ submitLoading ? t('register.btnSubmitLoading') : t('register.registerBtn') }}
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
  user-select: none;
  width: 100%;
  min-height: var(--app-viewport-height);
  background-color: #08090C;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow-x: hidden;
  overflow-y: auto;
  position: relative;
}

.left-logo {
  width: 680px;
  margin-right: clamp(64px, 7vw, 112px);
  min-width: 680px;
  z-index: 10;
  display: flex;
  align-items: center;
}

.register-form-box {
  box-sizing: border-box;
  min-width: 440px;
  width: 440px;
  padding: 48px 40px 40px;
  background-color: hsla(0, 0%, 100%, .1);
  border-radius: var(--radius-card);
  box-shadow: 0px 4px 4px 0px rgba(0, 0, 0, .25);
  border: 1px solid rgba(255, 255, 255, 0.05);
  z-index: 10;
}

.register-box-title {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  font-weight: 600;
  color: #FFF;
  margin-top: 4px;
}

.register-box-title__copy {
  min-width: 0;
  font-size: 18px;
  line-height: 1.35;
  overflow-wrap: normal;
}

.register-box-content {
  margin-top: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.input-wrapper {
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.input-row {
  display: flex;
  align-items: center;
  background-color: #313131;
  border: 1px solid #3A3B40;
  border-radius: var(--radius-card);
  height: 42px;
  padding: 0 14px;
  transition: border-color 0.2s;
  min-width: 0;
}

.input-row:focus-within {
  border-color: var(--color-primary);
}

.input-icon {
  color: #888A8F;
  flex-shrink: 0;
  margin-right: 12px;
}

.input-row input {
  height: 38px;
  flex: 1;
  background: transparent;
  border: none;
  outline: none;
  font-size: 14px;
  color: #fff;
  min-width: 0;
}

.input-row input::placeholder {
  color: #6A6C71;
}

.eye-btn {
  background: none;
  border: none;
  cursor: pointer;
  padding: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: #888A8F;
  border-radius: 4px;
  width: 24px;
  height: 24px;
  flex-shrink: 0;
}

.eye-btn:hover {
  color: #fff;
  background-color: rgba(255, 255, 255, 0.1);
}

.captcha-row {
  display: flex;
  gap: 12px;
  width: 100%;
}

.captcha-input {
  flex: 1;
}

.send-code-btn {
  flex-shrink: 0;
  min-width: 100px;
  height: 42px;
  padding: 0 12px;
  background: transparent;
  border: 1px solid rgba(139, 92, 246, 0.4);
  border-radius: 21px;
  color: rgba(196, 181, 253, 0.85);
  font-size: 13px;
  font-weight: 400;
  cursor: pointer;
  transition: background 0.2s, border-color 0.2s, color 0.2s;
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
  background: rgba(139, 92, 246, 0.08);
  border-color: rgba(139, 92, 246, 0.6);
  color: #ddd6fe;
}

.send-code-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.input-wrapper.has-error .input-row {
  border-color: #f85149;
}

.error-msg {
  font-size: 12px;
  color: #f85149;
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
  gap: 4px;
}

.footer-text {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.45);
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
  height: 42px !important;
  border-radius: 21px;
  font-size: 15px;
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
    flex-direction: column;
    justify-content: flex-start;
    gap: 20px;
    box-sizing: border-box;
    padding: calc(24px + var(--app-safe-top)) max(20px, var(--app-safe-right)) calc(24px + var(--app-safe-bottom)) max(20px, var(--app-safe-left));
  }

  .left-logo {
    width: auto;
    min-width: 0;
    margin: 0;
  }

  .register-form-box {
    width: min(440px, 100%);
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
    padding: 24px 16px;
  }

  .register-box-title__copy {
    font-size: 20px;
  }

  .register-box-content {
    gap: 16px;
    margin-top: 20px;
  }

  .captcha-row {
    flex-direction: column;
    gap: 8px;
  }

  .send-code-btn {
    width: 100%;
  }

  .input-row,
  .send-code-btn,
  .submit-btn,
  .register-success-btn {
    min-height: 44px;
  }

  .eye-btn {
    min-width: 36px;
    min-height: 36px;
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
</style>

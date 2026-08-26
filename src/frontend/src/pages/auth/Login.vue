<script setup lang="ts">
import { ref, reactive, computed, nextTick, onMounted, onUnmounted } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ElMessage, ElMessageBox } from 'element-plus'
import { Mail, Lock, Eye, EyeOff } from 'lucide-vue-next'
import { api } from '../../lib/api'
import { useAuth, setStoredOrgKey, fetchCurrentUser } from '../../composables/useAuth'
import { useLocaleSwitch } from '../../composables/useLocaleSwitch'
import {
  clearLoginLocaleSelection,
  setAuthenticatedLocaleApplicationSuppressed,
  setLoginLocaleSelection,
  setPendingLoginLocale,
} from '../../i18n'
import { useTurnstileConfig } from '../../composables/useTurnstileConfig'
import AuthBackdrop from '../../components/auth/AuthBackdrop.vue'
import AuthBrandPanel from '../../components/auth/AuthBrandPanel.vue'
import AuthTurnstileField from '../../components/auth/AuthTurnstileField.vue'
import EmailCodeLoginForm from '../../components/auth/EmailCodeLoginForm.vue'
import LanguageSwitcher from '../../components/LanguageSwitcher.vue'
import type { EmailCodeLoginData } from '../../lib/emailCodeLoginApi'
import ResetPasswordCard from '../../components/auth/ResetPasswordCard.vue'
import { fetchDeployProfile, resolvePostLoginPath } from '../../composables/useDeployProfile'
import { appConfig } from '../../lib/appConfig'
import { resolveSafeLoginRedirect } from '../../lib/loginNavigation'
import { trackAppEvent } from '../../lib/analytics'
import {
  consumeSessionNotice,
  sessionNoticeMessageKey,
} from '../../lib/sessionNotice'

const emailSignupEnabled = ref(false)
const passwordResetAvailable = ref(false)
const emailCodeLoginAvailable = ref(false)
const showEula = appConfig.showEula
const { t, locale } = useI18n()
const router = useRouter()
const route = useRoute()
const sessionNoticeDismissed = ref(false)
const sessionNoticeReason = ref(consumeSessionNotice())

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

const { setUser } = useAuth()
setAuthenticatedLocaleApplicationSuppressed(true)
const { syncAuthenticatedLocale } = useLocaleSwitch()
const explicitlySelectedLocale = ref<string | null>(null)
const turnstileToken = ref('')
const turnstileError = ref('')
const turnstileErrorCode = ref('')
const turnstileFieldRef = ref<InstanceType<typeof AuthTurnstileField> | null>(null)
const googleEnabled = ref(false)
const googleLoginUrl = ref('/accounts/google/login/?process=login')
const googleLoading = ref(false)

// Session invalid error codes that should show a dialog
const SESSION_INVALID_CODES = [
  'OTHER_DEVICE_LOGIN',
  'PASSWORD_CHANGED',
  'ACCOUNT_DISABLED',
  'TOKEN_REUSED',
]

const formItems = reactive({
  email: {
    value: '',
    required: true,
    prop: 'email',
    icon: 'email',
    type: 'text' as const,
    placeholder: '',
    errorMsg: '',
    showError: false,
  },
  password: {
    value: '',
    required: true,
    prop: 'password',
    placeholder: '',
    type: 'password' as const,
    icon: 'password',
    errorMsg: '',
    showError: false,
  },
})

const emailFromQuery = route.query.email
if (typeof emailFromQuery === 'string' && emailFromQuery.trim()) {
  formItems.email.value = emailFromQuery.trim().toLowerCase()
}

const sessionNoticeMessage = computed(() => {
  if (sessionNoticeDismissed.value) return ''
  const key = sessionNoticeMessageKey(sessionNoticeReason.value)
  return key ? t(key) : ''
})

const sessionNoticeTone = computed(() => (
  sessionNoticeReason.value === 'TOKEN_EXPIRED' || sessionNoticeReason.value === 'REFRESH_EXPIRED'
    ? 'info'
    : 'warning'
))

function dismissSessionNotice() {
  sessionNoticeDismissed.value = true
}

// Initialize placeholders from i18n
formItems.email.placeholder = t('login.emailPh')
formItems.password.placeholder = t('login.passwordPh')
const submitLoading = ref(false)
const showPassword = ref(false)
const cardView = ref<'login' | 'reset'>('login')
type AuthMode = 'password' | 'email-code'

const authMode = ref<AuthMode>('password')
const authModeTabs = ref<Partial<Record<AuthMode, HTMLButtonElement>>>({})
const resetStep = ref<'request' | 'reset'>('request')
const regEmail = /^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/

function checkMail(value: string) {
  if (!value.trim()) {
    return t('login.emailErrRequired')
  }
  if (!regEmail.test(value)) {
    return t('login.emailErrFormat')
  }
  return ''
}

function checkPasswordRequired(value: string) {
  if (!value) {
    return t('login.passwordErrRequired')
  }
  return ''
}

function setAuthModeTabRef(mode: AuthMode, element: Element | null) {
  if (element instanceof HTMLButtonElement) {
    authModeTabs.value[mode] = element
  }
}

async function selectAuthMode(mode: AuthMode, focusTab = false) {
  authMode.value = mode
  if (!focusTab) return
  await nextTick()
  authModeTabs.value[mode]?.focus()
}

function onAuthModeKeydown(event: KeyboardEvent) {
  const modes: AuthMode[] = ['password', 'email-code']
  const currentIndex = modes.indexOf(authMode.value)
  let nextMode: AuthMode | undefined

  switch (event.key) {
    case 'ArrowRight':
    case 'ArrowDown':
      nextMode = modes[(currentIndex + 1) % modes.length]
      break
    case 'ArrowLeft':
    case 'ArrowUp':
      nextMode = modes[(currentIndex - 1 + modes.length) % modes.length]
      break
    case 'Home':
      nextMode = modes[0]
      break
    case 'End':
      nextMode = modes[modes.length - 1]
      break
    default:
      return
  }

  event.preventDefault()
  void selectAuthMode(nextMode, true)
}

// Real-time email validation
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

// Real-time password presence validation
function validatePasswordPresenceOnInput() {
  const error = checkPasswordRequired(formItems.password.value)
  if (error && formItems.password.value) {
    formItems.password.errorMsg = error
    formItems.password.showError = true
  } else {
    formItems.password.errorMsg = ''
    formItems.password.showError = false
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

function validateForm() {
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

  const passwordError = checkPasswordRequired(formItems.password.value)
  if (passwordError) {
    formItems.password.errorMsg = passwordError
    formItems.password.showError = true
    hasError = true
  } else {
    formItems.password.errorMsg = ''
    formItems.password.showError = false
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

function showSessionErrorDialog(errorCode: string) {
  const message = t(sessionNoticeMessageKey(errorCode) || 'login.sessionExpired')
  ElMessageBox.alert(message, t('login.sessionExpired'), {
    confirmButtonText: t('login.btnSubmit'),
    type: 'warning',
  }).then(() => {
    router.push('/login')
  })
}

async function resolveLoginTargetPath(): Promise<string> {
  const redirect = resolveSafeLoginRedirect(route.query.redirect)
  if (redirect) return redirect
  return resolvePostLoginPath()
}

function syncExplicitLoginLocale() {
  const selectedLocale = explicitlySelectedLocale.value
  if (selectedLocale) {
    syncAuthenticatedLocale(selectedLocale)
    clearLoginLocaleSelection()
  }
}

async function handleSubmit() {
  if (submitLoading.value) return

  if (!validateForm()) return

  submitLoading.value = true
  setStoredOrgKey('')

  formItems.email.errorMsg = ''
  formItems.email.showError = false
  formItems.password.errorMsg = ''
  formItems.password.showError = false
  turnstileError.value = ''

  try {
    const postData = {
      email: formItems.email.value,
      password: formItems.password.value,
      ...buildTurnstilePayload(turnstileToken.value),
    }

    const res = await api<{
      code: string
      data: {
        user?: { id: number; email: string; username: string; is_staff?: boolean }
        roles?: string[]
        available_orgs?: Array<{ org_key: string; org_name: string; role: string }>
        message?: string
        error?: {
          fields?: Record<string, string[]>
          message?: string
        }
      }
      error?: {
        error_code?: string
        fields?: Record<string, string[]>
        message?: string
      }
    }>('/api/v1/auth/email-login', {
      method: 'POST',
      body: JSON.stringify(postData),
    })

    if (res.code !== '0000') {
      // Check for session invalid errors
      const errorCode = res.error?.error_code
      if (errorCode && SESSION_INVALID_CODES.includes(errorCode)) {
        showSessionErrorDialog(errorCode)
        return
      }

      // Error can be in either res.error or res.data.error
      const fields = res.error?.fields || res.data?.error?.fields
      if (fields && Object.keys(fields).length > 0) {
        handleFieldsError(fields)
      } else {
        ElMessage.error({ message: res.error?.message || res.data?.error?.message || t('login.msgLoginFailed'), grouping: true })
      }
      return
    }

    // Auto-login with the first available organization
    const orgs = res.data.available_orgs || []
    if (orgs.length > 0) {
      await completeLoginWithOrg(orgs[0].org_key)
      return
    }

    // No orgs - login successful (shouldn't happen normally)
    if (res.data.user) {
      setUser(res.data.user)
    }
    syncExplicitLoginLocale()
    trackAppEvent('login', { method: 'email' })
    router.push(await resolveLoginTargetPath())
  } catch (err: unknown) {
    const errObj = err as { status?: number; message?: string; errorCode?: string; code?: string; fields?: Record<string, string[]> }

    // Check for session invalid errors
    if (errObj.errorCode && SESSION_INVALID_CODES.includes(errObj.errorCode)) {
      showSessionErrorDialog(errObj.errorCode)
      return
    }

    const fields = errObj.fields
    if (fields && Object.keys(fields).length > 0) {
      handleFieldsError(fields)
    } else {
      resetTurnstile()
      ElMessage.error({ message: errObj.message || t('login.msgLoginFailed'), grouping: true })
    }
  } finally {
    submitLoading.value = false
  }
}

async function completeLoginWithOrg(orgKey: string) {
  try {
    const res = await api<{
      code: string
      status?: number
      data: {
        user?: { id: number; email: string; username: string; is_staff?: boolean }
        selected_org?: { org_key: string; org_name: string; role: string }
        message?: string
        code?: string
        error?: {
          message?: string
        }
      }
      error?: {
        message?: string
      }
    }>('/api/v1/auth/org-select', {
      method: 'POST',
      body: JSON.stringify({ org_key: orgKey }),
    })

    if (res.code !== '0000') {
      const isSessionExpired = res.status === 401 || res.data?.code === '1001'
      if (isSessionExpired) {
        ElMessage.error({ message: t('login.sessionExpired'), grouping: true })
        resetTurnstile()
        return
      }
      ElMessage.error({ message: res.error?.message || res.data?.error?.message || t('login.msgLoginFailed'), grouping: true })
      resetTurnstile()
      return
    }

    setStoredOrgKey(orgKey)
    await fetchCurrentUser()
    syncExplicitLoginLocale()

    trackAppEvent('login', { method: 'email' })
    router.push(await resolveLoginTargetPath())
  } catch (err: unknown) {
    const errObj = err as { message?: string; status?: number }
    if (errObj.status === 401) {
      ElMessage.error({ message: t('login.sessionExpired'), grouping: true })
      resetTurnstile()
    } else {
      ElMessage.error({ message: errObj.message || t('login.msgLoginFailed'), grouping: true })
      resetTurnstile()
    }
  }
}

async function handleEmailCodeVerified(data: EmailCodeLoginData) {
  const orgs = data.available_orgs || []
  if (orgs.length === 0) {
    ElMessage.error({ message: t('login.emailCodeNoOrganization'), grouping: true })
    return
  }
  await completeLoginWithOrg(orgs[0].org_key)
}

function handleFieldsError(fields?: Record<string, string[]>) {
  if (!fields) return

  if (fields.turnstile_token) {
    resetTurnstile()
  } else if (fields.password && isTurnstileReady.value) {
    // Turnstile tokens are single-use; refresh while the user corrects their password.
    resetTurnstile()
  }

  // Known error message translations
  const errorMessageMap: Record<string, string> = {
    'Invalid or expired human verification': t('login.captchaInvalid'),
    'Incorrect password': t('login.passwordErrIncorrect'),
  }

  for (const [fieldName, messages] of Object.entries(fields)) {
    let message = Array.isArray(messages) ? messages[0] : messages
    // Translate known error messages
    if (errorMessageMap[message]) {
      message = errorMessageMap[message]
    }
    switch (fieldName) {
      case 'email':
        formItems.email.errorMsg = message
        formItems.email.showError = true
        break
      case 'password':
        formItems.password.errorMsg = message
        formItems.password.showError = true
        break
      case 'turnstile_token':
        turnstileError.value = message
        break
      default:
        break
    }
  }
}

function goRegister() {
  router.push('/register')
}

function goForgetPwd() {
  if (!passwordResetAvailable.value) return
  cardView.value = 'reset'
  resetStep.value = 'request'
}

function backToLogin(email?: string) {
  if (email) {
    formItems.email.value = email
  }
  cardView.value = 'login'
  resetStep.value = 'request'
}

function onResetStepChange(step: 'request' | 'reset') {
  resetStep.value = step
}

const cardTitle = computed(() => {
  if (cardView.value === 'login') return t('login.welcomeTitle')
  if (resetStep.value === 'reset') return t('findPwd.updateTitle')
  return t('findPwd.welcomeTitle')
})

const credentialsPresent = computed(() => (
  checkMail(formItems.email.value) === '' &&
  checkPasswordRequired(formItems.password.value) === ''
))

const canSubmitLogin = computed(() => {
  if (submitLoading.value) return false
  if (!credentialsPresent.value) return false
  if (isTurnstilePending.value) return false
  if (isTurnstileBlocked.value) return false
  if (isTurnstileReady.value) return Boolean(turnstileToken.value)
  return true
})

function handleLocaleChange(locale: string) {
  explicitlySelectedLocale.value = locale
  setLoginLocaleSelection(locale)
  formItems.email.placeholder = t('login.emailPh')
  formItems.password.placeholder = t('login.passwordPh')
}

async function loadGoogleConfig() {
  try {
    const res = await api<{
      code: string
      data: { enabled: boolean; login_url?: string }
    }>('/api/v1/auth/google/config')
    if (res.code === '0000' && res.data?.enabled) {
      googleEnabled.value = true
      if (res.data.login_url) {
        googleLoginUrl.value = res.data.login_url
      }
    }
  } catch {
    googleEnabled.value = false
  }
}

function startGoogleLogin() {
  if (!googleEnabled.value || googleLoading.value) return
  googleLoading.value = true
  setStoredOrgKey('')
  setPendingLoginLocale(String(locale.value))
  window.location.assign(googleLoginUrl.value)
}

onUnmounted(() => {
  setAuthenticatedLocaleApplicationSuppressed(false)
})

onMounted(async () => {
  turnstileToken.value = ''
  void loadGoogleConfig()
  const profile = await fetchDeployProfile()
  emailSignupEnabled.value = !!profile?.email_signup_enabled
  passwordResetAvailable.value = !!profile?.password_reset_available
  emailCodeLoginAvailable.value = !!profile?.email_code_login_available
  await loadTurnstileConfig()
})
</script>

<template>
  <div class="login-container">
    <AuthBackdrop />

    <!-- Left Banner -->
    <div class="left-logo">
      <div class="flex flex-col items-start w-full">
        <AuthBrandPanel
          :description="t('login.brandDesc')"
          :slogan="t('login.brandSlogan')"
        />
      </div>
    </div>

    <!-- Login Form Box -->
    <div class="login-form-box">
      <div class="login-box-title">
        <span class="login-box-title__copy">
          {{ cardTitle }}
        </span>
        <LanguageSwitcher
          variant="auth"
          @change="handleLocaleChange"
        />
      </div>

      <Transition
        name="card-view-fade"
        mode="out-in"
      >
        <ResetPasswordCard
          v-if="cardView === 'reset'"
          key="reset"
          class="login-box-content"
          :initial-email="formItems.email.value"
          @back-to-login="backToLogin"
          @update:step="onResetStepChange"
        />

        <div
          v-else
          key="login"
          class="login-box-content"
        >
          <ElAlert
            v-if="sessionNoticeMessage"
            class="session-alert"
            :class="`session-alert--${sessionNoticeTone}`"
            :type="sessionNoticeTone"
            :title="sessionNoticeMessage"
            show-icon
            :closable="true"
            @close="dismissSessionNotice"
          />

          <div
            v-if="emailCodeLoginAvailable"
            class="login-method-tabs"
            role="tablist"
            :aria-label="t('login.methodLabel')"
            aria-orientation="horizontal"
          >
            <button
              id="login-method-tab-password"
              :ref="element => setAuthModeTabRef('password', element)"
              type="button"
              class="login-method-tabs__tab"
              :class="{ 'is-active': authMode === 'password' }"
              role="tab"
              :aria-selected="authMode === 'password'"
              aria-controls="login-method-panel"
              :tabindex="authMode === 'password' ? 0 : -1"
              @click="selectAuthMode('password')"
              @keydown="onAuthModeKeydown"
            >
              {{ t('login.passwordMethod') }}
            </button>
            <button
              id="login-method-tab-email-code"
              :ref="element => setAuthModeTabRef('email-code', element)"
              type="button"
              class="login-method-tabs__tab"
              :class="{ 'is-active': authMode === 'email-code' }"
              role="tab"
              :aria-selected="authMode === 'email-code'"
              aria-controls="login-method-panel"
              :tabindex="authMode === 'email-code' ? 0 : -1"
              @click="selectAuthMode('email-code')"
              @keydown="onAuthModeKeydown"
            >
              {{ t('login.emailCodeMethod') }}
            </button>
          </div>

          <div
            id="login-method-panel"
            class="login-method-panel"
            role="tabpanel"
            :aria-labelledby="`login-method-tab-${authMode}`"
          >
            <!-- Email -->
            <div
              v-if="authMode === 'password'"
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
                  :placeholder="formItems.email.placeholder"
                  tabindex="1"
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

            <!-- Password -->
            <div
              v-if="authMode === 'password'"
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
                  :placeholder="formItems.password.placeholder"
                  tabindex="2"
                  autocomplete="current-password"
                  @blur="validatePasswordPresenceOnInput"
                  @input="validatePasswordPresenceOnInput"
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
              <p
                v-if="formItems.password.showError"
                class="error-msg"
              >
                {{ formItems.password.errorMsg }}
              </p>
            </div>

            <AuthTurnstileField
              v-if="authMode === 'password'"
              :key="authTurnstileMountGeneration"
              ref="turnstileFieldRef"
              :pending="isTurnstilePending"
              :ready="isTurnstileReady"
              :blocked="isTurnstileBlocked"
              :verified="Boolean(turnstileToken)"
              :site-key="turnstileSiteKey"
              action="login"
              :loading-message="t('login.captchaLoading')"
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

            <div class="login-actions">
              <div v-if="authMode === 'password'">
                <!-- Submit Button -->
                <ElButton
                  type="primary"
                  class="submit-btn"
                  :disabled="submitLoading || !canSubmitLogin"
                  :loading="submitLoading"
                  @click="handleSubmit"
                >
                  {{ submitLoading ? t('login.btnSubmitLoading') : t('login.btnSubmit') }}
                </ElButton>
              </div>

              <EmailCodeLoginForm
                v-if="authMode === 'email-code'"
                v-model:email="formItems.email.value"
                @verified="handleEmailCodeVerified"
              />

              <!-- Forgot Password -->
              <div
                v-if="passwordResetAvailable"
                class="forgot-row"
              >
                <a
                  href="#"
                  class="forgot-link"
                  @click.prevent="goForgetPwd"
                >{{ t('login.forgotPwd') }}</a>
              </div>
            </div>
          </div>

          <!-- Divider -->
          <div
            v-if="googleEnabled"
            class="divider-row"
          >
            <div class="divider-line" />
            <span class="divider-text">{{ t('login.dividerOr') }}</span>
            <div class="divider-line" />
          </div>

          <!-- Third Party -->
          <div
            v-if="googleEnabled"
            class="google-signin-block"
          >
            <button
              type="button"
              class="google-btn"
              :disabled="googleLoading"
              @click="startGoogleLogin"
            >
              <svg
                width="18"
                height="18"
                viewBox="0 0 24 24"
                fill="none"
                xmlns="http://www.w3.org/2000/svg"
              >
                <path
                  d="M22.56 12.25C22.56 11.47 22.49 10.73 22.36 10H12V14.26H17.92C17.66 15.63 16.89 16.8 15.72 17.58V20.34H19.28C21.36 18.42 22.56 15.6 22.56 12.25Z"
                  fill="#4285F4"
                />
                <path
                  d="M12 23C14.97 23 17.46 22.02 19.28 20.34L15.72 17.58C14.74 18.24 13.48 18.66 12 18.66C9.13999 18.66 6.70999 16.73 5.83999 14.12H2.17999V16.96C3.98999 20.55 7.7 23 12 23Z"
                  fill="#34A853"
                />
                <path
                  d="M5.84 14.12C5.62 13.46 5.49 12.75 5.49 12C5.49 11.25 5.61 10.54 5.84 9.88001V7.04001H2.18C1.43 8.53001 1 10.22 1 12C1 13.78 1.43 15.47 2.18 16.96L5.84 14.12Z"
                  fill="#FBBC05"
                />
                <path
                  d="M12 5.34001C13.62 5.34001 15.06 5.89001 16.21 6.99001L19.36 3.84001C17.46 2.07001 14.97 1 12 1C7.7 1 3.99 3.45001 2.18 7.04001L5.84 9.88001C6.71 7.27001 9.14 5.34001 12 5.34001Z"
                  fill="#EA4335"
                />
              </svg>
              <span>{{ t('login.googleBtn') }}</span>
            </button>
          </div>

          <!-- Footer: Register + EULA -->
          <div
            v-if="emailSignupEnabled || showEula"
            class="login-footer"
          >
            <div
              v-if="emailSignupEnabled"
              class="footer-row"
            >
              <span class="footer-text">{{ t('login.noAccount') }}</span>
              <a
                href="#"
                class="footer-link sign-up-link"
                @click.prevent="goRegister"
              >{{ t('login.freeRegister') }}</a>
            </div>
            <p
              v-if="showEula"
              class="footer-legal"
            >
              {{ t('login.eulaText') }}
              <a
                class="footer-link"
                href="https://oneprocloud.com/eula"
                target="_blank"
                rel="noopener noreferrer"
              >
                {{ $t('login.eulaNoticeLinkText') }}
              </a>{{ t('login.eulaSuffix') }}
            </p>
          </div>
        </div>
      </Transition>
    </div>
  </div>
</template>

<style scoped>
.login-container {
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

.login-form-box {
  box-sizing: border-box;
  min-width: 440px;
  width: 440px;
  padding: 40px;
  background-color: hsla(0, 0%, 100%, .1);
  border-radius: var(--radius-card);
  box-shadow: 0px 4px 4px 0px rgba(0, 0, 0, .25);
  border: 1px solid rgba(255, 255, 255, 0.05);
  z-index: 10;
}

.login-box-title {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  align-items: center;
  gap: 12px;
  font-weight: 600;
  color: #FFF;
}

.login-box-title__copy {
  min-width: 0;
  font-size: 17px;
  line-height: 1.35;
  overflow-wrap: normal;
}

.login-box-content {
  margin-top: 24px;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.login-actions {
  display: flex;
  flex-direction: column;
}

.login-method-tabs {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  border-bottom: 1px solid rgba(255, 255, 255, 0.13);
}

.login-method-tabs__tab {
  position: relative;
  min-height: 44px;
  padding: 0 12px;
  color: rgba(255, 255, 255, 0.62);
  background: transparent;
  border: 0;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  transition: color 0.18s ease;
}

.login-method-tabs__tab::after {
  position: absolute;
  right: 20px;
  bottom: -1px;
  left: 20px;
  height: 2px;
  content: '';
  background: linear-gradient(90deg, var(--color-primary), var(--color-brand-violet-soft));
  border-radius: 999px;
  transform: scaleX(0);
  transform-origin: center;
  transition: transform 0.18s ease;
}

.login-method-tabs__tab:hover {
  color: rgba(255, 255, 255, 0.84);
}

.login-method-tabs__tab:active {
  color: #fff;
}

.login-method-tabs__tab.is-active {
  color: #fff;
  font-weight: 600;
}

.login-method-tabs__tab.is-active::after {
  transform: scaleX(1);
}

.login-method-tabs__tab:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.login-method-panel {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.session-alert {
  --el-alert-padding: 10px 12px;
  --session-alert-accent: var(--color-brand-violet-soft);
  --session-alert-background: color-mix(in srgb, var(--color-primary) 14%, transparent);
  --session-alert-border: color-mix(in srgb, var(--color-brand-violet-soft) 34%, transparent);
  --session-alert-text: color-mix(in srgb, var(--color-brand-violet-soft) 18%, white);
  background-color: var(--session-alert-background);
  border: 1px solid var(--session-alert-border);
  border-radius: var(--radius-card);
  line-height: 1.4;
  position: relative;
  padding-right: 42px;
}

.session-alert--warning {
  --session-alert-accent: color-mix(in srgb, var(--color-warning) 68%, white);
  --session-alert-background: color-mix(in srgb, var(--color-warning) 13%, transparent);
  --session-alert-border: color-mix(in srgb, var(--color-warning) 36%, transparent);
  --session-alert-text: color-mix(in srgb, var(--color-warning) 18%, white);
}

.session-alert :deep(.el-alert__title) {
  color: var(--session-alert-text);
  font-weight: 500;
  line-height: 20px;
}

.session-alert :deep(.el-alert__icon) {
  color: var(--session-alert-accent);
}

.session-alert :deep(.el-alert__close-btn) {
  top: 50%;
  right: 12px;
  width: 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
  color: color-mix(in srgb, var(--session-alert-text) 72%, transparent);
  transform: translateY(-50%);
  transition: background-color 0.2s, color 0.2s;
}

.session-alert :deep(.el-alert__close-btn:hover),
.session-alert :deep(.el-alert__close-btn:focus-visible) {
  background-color: color-mix(in srgb, var(--session-alert-accent) 16%, transparent);
  color: var(--session-alert-text);
}

/* Shared input styles */
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
  margin-left: 8px;
  color: #888A8F;
  border-radius: 4px;
  height: 24px;
  width: 24px;
  justify-content: center;
  transition: color 0.2s, background-color 0.2s;
}

.eye-btn:hover {
  color: #fff;
  background-color: rgba(255, 255, 255, 0.1);
}

/* Error state */
.input-wrapper.has-error .input-row {
  border-color: #f85149;
}

.error-msg {
  font-size: 12px;
  color: #f85149;
  padding-left: 2px;
}

/* Submit button */
.submit-btn {
  width: 100%;
  height: 42px !important;
  border-radius: 21px;
  font-size: 15px;
  font-weight: 500;
}

.btn-content {
  display: flex;
  align-items: center;
  gap: 8px;
}

.spin-icon {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}

/* Forgot password */
.forgot-row {
  text-align: right;
  padding-top: 4px;
}

.forgot-link {
  font-size: 12px;
  color: #fff;
  text-decoration: none;
  transition: color 0.2s;
}

.forgot-link:hover {
  color: #fff;
}

/* Divider */
.divider-row {
  display: flex;
  align-items: center;
  gap: 12px;
}

.divider-line {
  flex: 1;
  height: 1px;
  background: rgba(255, 255, 255, 0.1);
}

.divider-text {
  font-size: 12px;
  color: #fff;
}

/* Google login */
.google-signin-block {
  width: 100%;
}

.google-btn {
  width: 100%;
  height: 34px;
  background: #fff;
  border: none;
  border-radius: 21px;
  color: #333;
  font-size: 14px;
  font-weight: 500;
  cursor: pointer;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  transition: background 0.2s;
  box-shadow: 0 2px 10px rgba(0, 0, 0, 0.1);
}

.google-btn:hover:not(:disabled) {
  background: #f0f0f0;
}

.google-btn:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

/* Login footer area */
.login-footer {
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
  gap: 4px;
}

.footer-legal {
  width: 100%;
  margin: 0;
  text-align: center;
  font-size: 12px;
  line-height: 1.5;
  color: rgba(255, 255, 255, 0.38);
}

.footer-legal .footer-link {
  font-size: 12px;
  color: rgba(255, 255, 255, 0.52);
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
}

.footer-link:hover {
  color: #fff;
}

.sign-up-link {
  color: var(--color-primary);
  font-weight: 500;
}

.sign-up-link:hover {
  color: #c4b5fd;
}

.card-view-fade-enter-active,
.card-view-fade-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.card-view-fade-enter-from {
  opacity: 0;
  transform: translateX(12px);
}

.card-view-fade-leave-to {
  opacity: 0;
  transform: translateX(-12px);
}

@media (max-width: 1279.98px) {
  .login-container {
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

  .login-form-box {
    width: min(440px, 100%);
    min-width: 0;
    padding: 32px 28px;
  }
}

@media (max-width: 479.98px) {
  .login-container {
    gap: 14px;
    padding: calc(14px + var(--app-safe-top)) max(12px, var(--app-safe-right)) calc(16px + var(--app-safe-bottom)) max(12px, var(--app-safe-left));
  }

  .login-form-box {
    padding: 24px 16px;
  }

  .login-box-title__copy {
    font-size: 20px;
  }

  .login-box-content {
    gap: 16px;
    margin-top: 20px;
  }

  .input-row,
  .submit-btn,
  .google-btn {
    min-height: 44px;
  }

  .eye-btn {
    min-width: 36px;
    min-height: 36px;
  }
}

@media (max-width: 479.98px) and (min-height: 720px) {
  .login-container {
    padding-top: calc(clamp(32px, 6dvh, 48px) + var(--app-safe-top));
  }
}
</style>

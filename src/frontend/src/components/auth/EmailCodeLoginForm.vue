<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { KeyRound, Mail } from 'lucide-vue-next'
import { useI18n } from 'vue-i18n'

import { isAbortError, type ApiError } from '../../lib/api'
import { notifyError, notifySuccess, notifyWarning } from '../../lib/notify'
import {
  sendEmailLoginCode,
  verifyEmailLoginCode,
  type EmailCodeLoginData,
} from '../../lib/emailCodeLoginApi'

const props = defineProps<{
  email?: string
  initialEmail?: string
}>()

const emit = defineEmits<{
  verified: [data: EmailCodeLoginData]
  'update:email': [value: string]
}>()

const { t } = useI18n()
const COOLDOWN_STORAGE_KEY = 'hfl.email_code_login.cooldowns'
const DEFAULT_COOLDOWN_SECONDS = 60
const DEFAULT_CODE_TTL_MS = 10 * 60 * 1000

type CooldownRecord = {
  resendAvailableAt: number
  codeExpiresAt: number
  codeIssued: boolean
}

const email = ref(props.email ?? props.initialEmail ?? '')
const code = ref('')
const emailError = ref('')
const codeError = ref('')
const issuedEmail = ref('')
const codeIssued = ref(false)
const sending = ref(false)
const verifying = ref(false)
const cooldownSeconds = ref(0)
const activeCooldown = ref<CooldownRecord | undefined>()
const codeInputRef = ref<HTMLInputElement | null>(null)
let timer: number | null = null
let sendController: AbortController | null = null
let verifyController: AbortController | null = null
let restoreGeneration = 0

const normalizedEmail = computed(() => email.value.trim().toLowerCase())
const emailValid = computed(() => /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(normalizedEmail.value))
const hasUsableCode = computed(() => (
  codeIssued.value
  && issuedEmail.value === normalizedEmail.value
))
const sendDisabled = computed(() => (
  sending.value
  || verifying.value
  || !emailValid.value
  || cooldownSeconds.value > 0
))
const canVerify = computed(() => (
  hasUsableCode.value
  && code.value.length === 6
  && !sending.value
  && !verifying.value
))
const sendLabel = computed(() => {
  if (sending.value) return t('login.emailCodeSending')
  if (cooldownSeconds.value > 0) {
    return t('login.emailCodeResendIn', { seconds: cooldownSeconds.value })
  }
  return hasUsableCode.value ? t('login.emailCodeResend') : t('login.emailCodeSend')
})

function readCooldowns(): Record<string, CooldownRecord> {
  try {
    const value = JSON.parse(sessionStorage.getItem(COOLDOWN_STORAGE_KEY) || '{}')
    return value && typeof value === 'object' ? value : {}
  } catch {
    return {}
  }
}

function writeCooldowns(value: Record<string, CooldownRecord>) {
  try {
    sessionStorage.setItem(COOLDOWN_STORAGE_KEY, JSON.stringify(value))
  } catch {
    // The server remains authoritative when browser storage is unavailable.
  }
}

async function emailFingerprint(value: string): Promise<string> {
  const normalized = value.trim().toLowerCase()
  if (globalThis.crypto?.subtle) {
    const digest = await globalThis.crypto.subtle.digest(
      'SHA-256',
      new TextEncoder().encode(normalized),
    )
    return Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')
  }
  let hash = 2166136261
  for (let index = 0; index < normalized.length; index += 1) {
    hash ^= normalized.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return `fallback-${(hash >>> 0).toString(16)}`
}

async function persistCooldown(
  retryAfter: number,
  codeWasIssued: boolean,
  codeTtlSeconds = DEFAULT_CODE_TTL_MS / 1000,
) {
  const fingerprint = await emailFingerprint(normalizedEmail.value)
  const now = Date.now()
  const records = readCooldowns()
  records[fingerprint] = {
    resendAvailableAt: now + Math.max(1, retryAfter) * 1000,
    codeExpiresAt: now + Math.max(1, codeTtlSeconds) * 1000,
    codeIssued: codeWasIssued,
  }
  writeCooldowns(records)
  activeCooldown.value = records[fingerprint]
  refreshCountdown(activeCooldown.value)
}

function refreshCountdown(record?: CooldownRecord) {
  const now = Date.now()
  cooldownSeconds.value = record
    ? Math.max(0, Math.ceil((record.resendAvailableAt - now) / 1000))
    : 0
}

async function restoreCooldown(restoreIssuedCode = true) {
  const generation = ++restoreGeneration
  if (!emailValid.value) {
    activeCooldown.value = undefined
    cooldownSeconds.value = 0
    return
  }
  const fingerprint = await emailFingerprint(normalizedEmail.value)
  if (generation !== restoreGeneration) return
  const record = readCooldowns()[fingerprint]
  activeCooldown.value = record
  refreshCountdown(record)
  if (
    restoreIssuedCode
    && record?.codeIssued
    && record.codeExpiresAt > Date.now()
  ) {
    issuedEmail.value = normalizedEmail.value
    codeIssued.value = true
  }
}

function validateEmail(): boolean {
  emailError.value = emailValid.value ? '' : t('login.emailErrFormat')
  return emailValid.value
}

function sanitizeCode(event: Event) {
  const target = event.target as HTMLInputElement
  code.value = target.value.replace(/\D/g, '').slice(0, 6)
  target.value = code.value
  if (codeError.value) codeError.value = ''
}

function errorCode(error: unknown): string {
  return String((error as ApiError | undefined)?.errorCode || '')
}

function errorRetryAfter(error: unknown): number {
  const detail = (error as ApiError | undefined)?.detail
  if (!detail || typeof detail !== 'object') return DEFAULT_COOLDOWN_SECONDS
  const outer = detail as Record<string, unknown>
  const data = outer.data && typeof outer.data === 'object'
    ? outer.data as Record<string, unknown>
    : outer
  const errorData = data.error && typeof data.error === 'object'
    ? data.error as Record<string, unknown>
    : data
  const value = Number(errorData.retry_after)
  return Number.isFinite(value) && value > 0 ? value : DEFAULT_COOLDOWN_SECONDS
}

async function markStoredCodeUnusable() {
  const fingerprint = await emailFingerprint(normalizedEmail.value)
  const records = readCooldowns()
  if (!records[fingerprint]) return
  records[fingerprint].codeIssued = false
  writeCooldowns(records)
  activeCooldown.value = records[fingerprint]
}

async function sendCode() {
  if (sendDisabled.value || !validateEmail()) return
  sending.value = true
  emailError.value = ''
  codeError.value = ''
  sendController?.abort()
  sendController = new AbortController()
  const requestedEmail = normalizedEmail.value
  try {
    const response = await sendEmailLoginCode(requestedEmail, sendController.signal)
    if (normalizedEmail.value !== requestedEmail) return
    issuedEmail.value = requestedEmail
    codeIssued.value = true
    code.value = ''
    notifySuccess({
      title: t('login.emailCodeRequestReceivedTitle'),
      message: t('login.emailCodeGenericSent'),
      dedupeKey: 'auth:email-code:send:success',
      duration: 6000,
    })
    await persistCooldown(
      response.data.retry_after || DEFAULT_COOLDOWN_SECONDS,
      true,
      response.data.expires_in,
    )
    await nextTick()
    codeInputRef.value?.focus()
  } catch (error) {
    if (isAbortError(error)) return
    const apiCode = errorCode(error)
    if (apiCode === 'EMAIL_CODE_RATE_LIMITED') {
      await persistCooldown(errorRetryAfter(error), false)
      issuedEmail.value = ''
      codeIssued.value = false
      code.value = ''
      notifyWarning({
        message: t('login.emailCodeRateLimited'),
        dedupeKey: 'auth:email-code:send:rate-limited',
      })
    } else if (apiCode === 'INVALID_EMAIL') {
      emailError.value = t('login.emailErrFormat')
    } else {
      notifyError({
        message: t('login.emailCodeSendFailed'),
        dedupeKey: 'auth:email-code:send:failed',
      })
    }
  } finally {
    sending.value = false
  }
}

async function verifyCode() {
  if (!canVerify.value) return
  verifying.value = true
  codeError.value = ''
  verifyController?.abort()
  verifyController = new AbortController()
  try {
    const response = await verifyEmailLoginCode(
      normalizedEmail.value,
      code.value,
      verifyController.signal,
    )
    emit('verified', response.data)
  } catch (error) {
    if (isAbortError(error)) return
    const apiCode = errorCode(error)
    if (apiCode === 'EMAIL_CODE_ATTEMPTS_EXCEEDED') {
      codeError.value = t('login.emailCodeAttemptsExceeded')
      codeIssued.value = false
      await markStoredCodeUnusable()
    } else if (apiCode === 'EMAIL_CODE_RATE_LIMITED') {
      codeError.value = t('login.emailCodeRateLimited')
    } else {
      codeError.value = t('login.emailCodeInvalid')
    }
    code.value = ''
    await nextTick()
    codeInputRef.value?.focus()
  } finally {
    verifying.value = false
  }
}

watch(email, value => {
  emit('update:email', value)
})
watch(normalizedEmail, currentEmail => {
  if (codeIssued.value && issuedEmail.value === currentEmail) return
  verifyController?.abort()
  code.value = ''
  codeIssued.value = false
  issuedEmail.value = ''
  codeError.value = ''
  void restoreCooldown()
})
watch(() => props.email, value => {
  if (value !== undefined && value !== email.value) email.value = value
})
watch(() => props.initialEmail, value => {
  if (props.email === undefined && value !== undefined && value !== email.value) {
    email.value = value
  }
})

onMounted(() => {
  void restoreCooldown()
  timer = window.setInterval(() => {
    refreshCountdown(activeCooldown.value)
  }, 1000)
})

onBeforeUnmount(() => {
  if (timer !== null) window.clearInterval(timer)
  sendController?.abort()
  verifyController?.abort()
})
</script>

<template>
  <div class="email-code-login-form">
    <div
      class="input-wrapper"
      :class="{ 'has-error': emailError }"
    >
      <label
        class="sr-only"
        for="email-code-login-email"
      >{{ t('login.emailPh') }}</label>
      <div class="input-row">
        <Mail
          class="input-icon"
          :size="18"
          aria-hidden="true"
        />
        <input
          id="email-code-login-email"
          v-model="email"
          type="email"
          :placeholder="t('login.emailPh')"
          autocomplete="email"
          :disabled="sending || verifying"
          :aria-invalid="Boolean(emailError)"
          @input="validateEmail"
          @blur="validateEmail"
          @keyup.enter="sendCode"
        >
      </div>
      <p
        v-if="emailError"
        class="error-msg"
        role="alert"
      >
        {{ emailError }}
      </p>
    </div>

    <div
      class="input-wrapper"
      :class="{ 'has-error': codeError }"
    >
      <label
        class="sr-only"
        for="email-code-login-code"
      >{{ t('login.emailCodePlaceholder') }}</label>
      <div class="input-row email-code-login-form__code-row">
        <KeyRound
          class="input-icon"
          :size="18"
          aria-hidden="true"
        />
        <input
          id="email-code-login-code"
          ref="codeInputRef"
          :value="code"
          type="text"
          inputmode="numeric"
          autocomplete="one-time-code"
          maxlength="6"
          :placeholder="t('login.emailCodePlaceholder')"
          :disabled="!hasUsableCode || sending || verifying"
          :aria-invalid="Boolean(codeError)"
          @input="sanitizeCode"
          @keyup.enter="verifyCode"
        >
        <button
          type="button"
          class="email-code-login-form__send"
          :disabled="sendDisabled"
          @click="sendCode"
        >
          {{ sendLabel }}
        </button>
      </div>
      <p
        v-if="codeError"
        class="error-msg"
        role="alert"
      >
        {{ codeError }}
      </p>
    </div>

    <ElButton
      type="primary"
      class="submit-btn"
      :disabled="!canVerify"
      :loading="verifying"
      @click="verifyCode"
    >
      {{ verifying ? t('login.btnSubmitLoading') : t('login.btnSubmit') }}
    </ElButton>
  </div>
</template>

<style scoped>
.email-code-login-form {
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
  height: 42px;
  display: flex;
  align-items: center;
  padding: 0 14px;
  background-color: #313131;
  border: 1px solid #3a3b40;
  border-radius: var(--radius-card);
  transition: border-color 0.2s;
}

.input-row:focus-within {
  border-color: var(--color-primary);
}

.input-icon {
  margin-right: 12px;
  flex-shrink: 0;
  color: #888a8f;
}

.input-row input {
  height: 38px;
  min-width: 0;
  flex: 1;
  color: #fff;
  background: transparent;
  border: 0;
  outline: 0;
  font-size: 14px;
}

.input-row input::placeholder {
  color: #6a6c71;
}

.input-row input:disabled {
  opacity: 0.72;
}

.input-wrapper.has-error .input-row {
  border-color: #f85149;
}

.error-msg {
  margin: 0;
  padding-left: 2px;
  color: #f85149;
  font-size: 12px;
}

.email-code-login-form__send {
  min-height: 40px;
  flex: 0 0 auto;
  color: var(--color-brand-violet-soft);
  background: transparent;
  border: 0;
  border-radius: 6px;
  font-size: 12px;
  cursor: pointer;
  min-width: 88px;
  padding: 0 8px 0 12px;
  border-left: 1px solid #4a4b51;
  border-radius: 0;
}

.email-code-login-form__send:hover:not(:disabled) {
  color: #fff;
}

.email-code-login-form__send:active:not(:disabled) {
  background: rgba(109, 40, 217, 0.14);
}

.email-code-login-form__send:focus-visible {
  outline: 2px solid var(--color-primary);
  outline-offset: 2px;
}

.email-code-login-form__send:disabled {
  color: #777980;
  cursor: not-allowed;
}

.submit-btn {
  width: 100%;
  height: 42px !important;
  border-radius: 21px;
  font-size: 15px;
  font-weight: 500;
}

.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { api } from '../../lib/api'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

const verifiedReason = ref('')
const checking = ref(true)

const message = computed(() => {
  if (checking.value) return t('login.googleErrorChecking')
  switch (verifiedReason.value) {
    case 'disabled':
      return t('login.googleErrorOAuthDisabled')
    case 'no_email':
      return t('login.googleErrorNoEmail')
    case 'account_disabled':
      return t('login.googleErrorDisabled')
    case 'provision_failed':
      return t('login.googleErrorProvision')
    case 'not_authenticated':
      return t('login.googleErrorNotAuthenticated')
    case 'invalid_grant':
      return t('login.googleErrorInvalidGrant')
    case 'state_lost':
      return t('login.googleErrorStateLost')
    default:
      return t('login.googleLoginFailed')
  }
})

function backToLogin() {
  router.push('/login')
}

onMounted(async () => {
  const eventId = typeof route.query.event_id === 'string'
    ? route.query.event_id
    : ''
  const query = { ...route.query }
  delete query.event_id
  delete query.reason

  if (Object.keys(query).length !== Object.keys(route.query).length) {
    try {
      await router.replace({
        path: route.path,
        query,
        hash: route.hash,
      })
    } catch {
      // URL cleanup failure must not prevent one-time event consumption.
    }
  }

  if (!eventId) {
    checking.value = false
    return
  }

  try {
    const response = await api<{
      code: string
      data?: {
        verified?: boolean
        reason?: string
      }
    }>('/api/v1/auth/google/error-events/consume', {
      method: 'POST',
      body: JSON.stringify({ event_id: eventId }),
    })
    if (response.code === '0000' && response.data?.verified === true) {
      verifiedReason.value = response.data.reason || ''
    }
  } catch {
    verifiedReason.value = ''
  } finally {
    checking.value = false
  }
})
</script>

<template>
  <div class="oauth-error">
    <div class="oauth-error-card">
      <h1>{{ t('login.googleErrorTitle') }}</h1>
      <p aria-live="polite">
        {{ message }}
      </p>
      <button
        type="button"
        class="back-btn"
        @click="backToLogin"
      >
        {{ t('login.backToLogin') }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.oauth-error {
  min-height: var(--app-viewport-height);
  display: flex;
  align-items: center;
  justify-content: center;
  background: #08090c;
  color: #fff;
}

.oauth-error-card {
  width: min(420px, 92vw);
  padding: 32px;
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.06);
  border: 1px solid #3a3b40;
  text-align: center;
}

.oauth-error-card h1 {
  margin: 0 0 12px;
  color: inherit;
  font-size: 20px;
}

.oauth-error-card p {
  margin: 0 0 24px;
  color: #b0b3b8;
  line-height: 1.5;
}

.back-btn {
  border: none;
  border-radius: 999px;
  padding: 10px 24px;
  background: #6366f1;
  color: #fff;
  cursor: pointer;
}
</style>

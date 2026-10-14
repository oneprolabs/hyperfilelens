<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api, apiErrorMessageI18n } from '../../lib/api'
import { currentUser, useAuth } from '../../composables/useAuth'

const { t } = useI18n()
const { logout } = useAuth()
const currentPassword = ref('')
const password = ref('')
const confirmation = ref('')
const saving = ref(false)
const error = ref('')

async function save() {
  if (saving.value) return
  error.value = ''
  if (!currentPassword.value || !password.value || password.value !== confirmation.value) {
    error.value = t('memberOnboarding.passwordValidation')
    return
  }
  saving.value = true
  try {
    await api('/api/v1/auth/change-password', {
      method: 'POST', body: JSON.stringify({
        current_password: currentPassword.value, new_password: password.value, confirm_password: confirmation.value,
      }),
    })
    currentPassword.value = password.value = confirmation.value = ''
    ElMessage.success(t('memberOnboarding.passwordSaved'))
    await logout()
  } catch (cause) {
    error.value = apiErrorMessageI18n(cause, t, t('memberOnboarding.operationFailed'))
  } finally { saving.value = false }
}
</script>

<template>
  <main class="account-setup-page">
    <section class="account-setup-card">
      <h1>{{ t('memberOnboarding.setupTitle') }}</h1>
      <p>{{ t('memberOnboarding.setupDescription') }}</p>
      <p class="account-setup-email">
        {{ currentUser?.email }}
      </p>
      <form @submit.prevent="save">
        <label for="setup-current">{{ t('memberOnboarding.temporaryPassword') }}</label>
        <el-input
          id="setup-current"
          v-model="currentPassword"
          type="password"
          show-password
          autocomplete="current-password"
          :disabled="saving"
        />
        <label for="setup-password">{{ t('memberOnboarding.newPassword') }}</label>
        <el-input
          id="setup-password"
          v-model="password"
          type="password"
          show-password
          autocomplete="new-password"
          :disabled="saving"
        />
        <p>{{ t('memberOnboarding.passwordRequirements') }}</p>
        <label for="setup-confirm">{{ t('memberOnboarding.confirmPassword') }}</label>
        <el-input
          id="setup-confirm"
          v-model="confirmation"
          type="password"
          show-password
          autocomplete="new-password"
          :disabled="saving"
        />
        <el-alert
          v-if="error"
          :title="error"
          type="error"
          :closable="false"
          show-icon
        />
        <div class="account-setup-actions">
          <el-button
            :disabled="saving"
            @click="logout"
          >
            {{ t('memberOnboarding.signOut') }}
          </el-button>
          <el-button
            type="primary"
            native-type="submit"
            :loading="saving"
          >
            {{ t('memberOnboarding.setPassword') }}
          </el-button>
        </div>
      </form>
    </section>
  </main>
</template>

<style scoped>
.account-setup-page { min-height: 100vh; display: grid; place-items: center; background: var(--color-content-bg); padding: 24px; }
.account-setup-card { width: min(100%, 480px); padding: 28px; border: 1px solid var(--color-border); border-radius: 12px; background: var(--color-card-bg); }
.account-setup-card h1 { font-size: 20px; font-weight: 600; margin: 0 0 12px; }
.account-setup-card p { color: var(--color-text-secondary); font-size: 13px; line-height: 1.6; margin: 8px 0 16px; }
.account-setup-card label { display: block; font-size: 13px; margin: 16px 0 8px; }
.account-setup-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 12px; margin-top: 24px; }
</style>

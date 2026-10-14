<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { ArrowLeft } from 'lucide-vue-next'
import { api, apiErrorMessageI18n } from '../../lib/api'
import { unwrapApiPayload } from '../../lib/parse'
import { fetchCurrentUser, setStoredOrgKey } from '../../composables/useAuth'

const { t } = useI18n()
const router = useRouter()
const name = ref('')
const busy = ref(true)
const saving = ref(false)
const allowed = ref(false)
const error = ref('')
async function load() {
  busy.value = true
  error.value = ''
  try {
    const policy = unwrapApiPayload<{ can_create: boolean }>(await api('/api/v1/iam/organization-creation-options/'))
    allowed.value = policy?.can_create === true
    if (!allowed.value) error.value = t('organizationCreation.unavailable')
  } catch (cause) { error.value = apiErrorMessageI18n(cause, t, t('organizationCreation.failed')) }
  finally { busy.value = false }
}
async function save() {
  if (saving.value || busy.value || !allowed.value) return
  if (!name.value.trim()) { error.value = t('organizationCreation.nameRequired'); return }
  saving.value = true
  error.value = ''
  try {
    const organization = unwrapApiPayload<{ key: string }>(await api('/api/v1/iam/create-organization/', {
      method: 'POST', body: JSON.stringify({ name: name.value.trim() }),
    }))
    if (!organization?.key) throw new Error(t('organizationCreation.failed'))
    setStoredOrgKey(organization.key)
    await fetchCurrentUser()
    window.location.assign('/node/organization')
  } catch (cause) { error.value = apiErrorMessageI18n(cause, t, t('organizationCreation.failed')) }
  finally { saving.value = false }
}
onMounted(load)
</script>

<template>
  <div class="fullscreen-form-fullscreen">
    <div class="fullscreen-form-page">
      <header class="fullscreen-form-header">
        <button
          type="button"
          class="fullscreen-form-header__back"
          :aria-label="t('common.back')"
          :disabled="saving"
          @click="router.back()"
        >
          <ArrowLeft :size="18" />
        </button>
        <div class="fullscreen-form-header__content">
          <h1 class="fullscreen-form-header__title">
            {{ t('organizationCreation.title') }}
          </h1>
          <p class="fullscreen-form-header__desc">
            {{ t('organizationCreation.description') }}
          </p>
        </div>
      </header>
      <div class="fullscreen-form-layout">
        <main class="fullscreen-form-main">
          <form @submit.prevent="save">
            <section class="fullscreen-form-card">
              <div class="fullscreen-form-section">
                <label
                  for="organization-name"
                  class="fullscreen-form-field__label"
                >{{ t('organizationCreation.name') }}</label>
                <el-input
                  id="organization-name"
                  v-model="name"
                  maxlength="200"
                  :disabled="busy || saving || !allowed"
                />
                <p class="fullscreen-form-field__hint">
                  {{ t('organizationCreation.notice') }}
                </p>
              </div>
            </section>
            <el-alert
              v-if="error"
              :title="error"
              type="error"
              :closable="false"
            />
          </form>
        </main>
      </div>
      <footer class="fullscreen-form-footer">
        <div />
        <div>
          <el-button
            :disabled="saving"
            @click="router.back()"
          >
            {{ t('common.cancel') }}
          </el-button>
          <el-button
            type="primary"
            :loading="saving"
            :disabled="busy || !allowed"
            @click="save"
          >
            {{ t('organizationCreation.title') }}
          </el-button>
        </div>
      </footer>
    </div>
  </div>
</template>

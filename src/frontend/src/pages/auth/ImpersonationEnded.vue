<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { impersonationContext, markImpersonationEnded, returnToAdmin } from '../../lib/impersonation'
const { t } = useI18n()
markImpersonationEnded()
const adminUrl = computed(() => {
  try {
    const value = new URL(impersonationContext.value?.admin_url || '')
    return ['https:', 'http:'].includes(value.protocol) ? value.href : ''
  } catch { return '' }
})
</script>
<template>
  <main class="impersonation-ended" role="status">
    <h1>{{ t('impersonation.endedTitle') }}</h1>
    <p>{{ t('impersonation.endedDescription') }}</p>
    <el-button v-if="adminUrl" type="primary" @click="returnToAdmin(adminUrl)">{{ t('impersonation.returnToAdmin') }}</el-button>
  </main>
</template>
<style scoped>
.impersonation-ended { max-width:36rem; margin:18vh auto; padding:24px; text-align:center; color:var(--text-primary); }
h1 { font-size:20px; font-weight:600; } p { margin:12px 0 24px; color:var(--text-secondary); }
</style>

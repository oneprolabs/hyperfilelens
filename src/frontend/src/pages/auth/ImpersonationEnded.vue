<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { LogOut } from 'lucide-vue-next'
import { impersonationContext, markImpersonationEnded, returnToAdmin } from '../../lib/impersonation'

const { t } = useI18n()
markImpersonationEnded()
const adminUrl = computed(() => {
  try {
    const value = new URL(impersonationContext.value?.admin_url || '')
    return ['https:', 'http:'].includes(value.protocol) && !value.username && !value.password ? value.href : ''
  } catch { return '' }
})
</script>

<template>
  <main
    class="impersonation-ended"
    aria-labelledby="impersonation-ended-title"
    aria-describedby="impersonation-ended-description"
  >
    <div class="impersonation-ended__content">
      <LogOut
        class="impersonation-ended__icon"
        :size="32"
        :stroke-width="1.75"
        aria-hidden="true"
        focusable="false"
      />
      <h1 id="impersonation-ended-title">
        {{ t('impersonation.endedTitle') }}
      </h1>
      <p id="impersonation-ended-description">
        {{ t(adminUrl ? 'impersonation.endedDescription' : 'impersonation.endedCloseTabDescription') }}
      </p>
      <el-button
        v-if="adminUrl"
        class="impersonation-ended__return"
        type="primary"
        @click="returnToAdmin(adminUrl)"
      >
        {{ t('impersonation.returnToAdmin') }}
      </el-button>
    </div>
  </main>
</template>

<style scoped>
.impersonation-ended {
  display: grid;
  min-height: var(--app-viewport-height, 100vh);
  box-sizing: border-box;
  padding: 48px 24px calc(48px + var(--app-viewport-height, 100vh) / 10);
  place-items: center;
  background: var(--color-content-bg);
  color: var(--color-text-primary);
  text-align: center;
}

.impersonation-ended__content {
  display: flex;
  width: 100%;
  max-width: 480px;
  min-width: 0;
  flex-direction: column;
  align-items: center;
}

.impersonation-ended__icon {
  flex-shrink: 0;
  margin-bottom: 16px;
  color: var(--color-text-secondary);
}

h1 {
  margin: 0;
  color: var(--color-text-title);
  font-size: 20px;
  font-weight: 600;
  line-height: 28px;
  text-wrap: balance;
  overflow-wrap: anywhere;
}

p {
  max-width: 352px;
  margin: 12px 0 0;
  color: var(--color-text-secondary);
  font-size: 14px;
  font-weight: 400;
  line-height: 22px;
  text-wrap: balance;
  overflow-wrap: anywhere;
}

/* Keep the result-page sizing above the shared Element Plus button overrides. */
.impersonation-ended .el-button.impersonation-ended__return:not(.is-link):not(.is-text) {
  max-width: 100%;
  height: auto;
  min-height: var(--impersonation-ended-button-height, 36px);
  margin: 24px 0 0;
  padding: 7px 16px;
  font-size: 14px;
  font-weight: 400;
  line-height: 20px;
  white-space: normal;
  overflow-wrap: anywhere;
  box-shadow: none !important;
}

@media (max-width: 640px) {
  .impersonation-ended {
    --impersonation-ended-button-height: 44px;
  }
}
</style>

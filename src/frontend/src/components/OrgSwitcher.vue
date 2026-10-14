<script setup lang="ts">
import { useId } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useOrganizationSwitcher } from '../composables/useOrganizationSwitcher'

const { t } = useI18n()
const router = useRouter()
const props = withDefaults(defineProps<{ variant?: 'desktop' | 'mobile' }>(), {
  variant: 'desktop',
})
const controlId = `org-switcher-${useId()}`
const {
  organizations,
  loading,
  currentKey,
  showSwitcher,
  switchOrganization,
  canCreate,
} = useOrganizationSwitcher()
function chooseOrganization(value: string) {
  if (value === '__create__') void router.push('/account/organizations/new')
  else void switchOrganization(value)
}
</script>

<template>
  <div
    v-if="showSwitcher"
    class="org-switcher"
    :class="`org-switcher--${props.variant}`"
  >
    <label
      class="org-switcher__label"
      :for="controlId"
    >{{ t('nav.orgSwitcher') }}</label>
    <select
      :id="controlId"
      class="org-switcher__select"
      :disabled="loading"
      :value="currentKey"
      @change="chooseOrganization(($event.target as HTMLSelectElement).value)"
    >
      <option
        v-for="org in organizations"
        :key="org.key"
        :value="org.key"
      >
        {{ org.name }}
      </option>
      <option
        v-if="canCreate"
        value="__create__"
      >
        {{ t('organizationCreation.title') }}
      </option>
    </select>
  </div>
</template>

<style scoped>
.org-switcher {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  margin-right: 8px;
}

.org-switcher__label {
  font-size: 12px;
  color: var(--tz-color, rgba(255, 255, 255, 0.72));
  white-space: nowrap;
}

.org-switcher__select {
  max-width: 180px;
  padding: 4px 8px;
  border-radius: 6px;
  border: 1px solid var(--tz-border, rgba(255, 255, 255, 0.12));
  background: var(--tz-bg, rgba(255, 255, 255, 0.06));
  color: var(--tnav-text, #fff);
  font-size: 12px;
}

.org-switcher--mobile {
  width: 100%;
  align-items: stretch;
  flex-direction: column;
  gap: 6px;
  margin-right: 0;
}

.org-switcher--mobile .org-switcher__label {
  color: var(--el-text-color-secondary);
}

.org-switcher--mobile .org-switcher__select {
  width: 100%;
  max-width: none;
  min-height: 44px;
  padding: 9px 12px;
  color: var(--sidebar-text, var(--el-text-color-primary));
  background: var(--el-fill-color-light);
  border-color: var(--sidebar-border, var(--el-border-color));
}

@media (min-width: 1024px) and (max-width: 1151.98px) {
  .org-switcher--desktop {
    gap: 0;
    margin-right: 4px;
  }

  .org-switcher--desktop .org-switcher__label {
    display: none;
  }

  .org-switcher--desktop .org-switcher__select {
    width: 104px;
  }
}
</style>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessageBox } from 'element-plus'
import { Building2, ChevronDown } from 'lucide-vue-next'
import { notifyError } from '../lib/notify'
import { useOrganizationSwitcher } from '../composables/useOrganizationSwitcher'

withDefaults(defineProps<{ variant?: 'desktop' | 'mobile' }>(), { variant: 'desktop' })
const { t } = useI18n()
const confirming = ref(false)
const { organizations, loading, switching, currentKey, currentName,
  showSwitcher, switchOrganization } = useOrganizationSwitcher()

async function chooseOrganization(value: string) {
  if (value === currentKey.value || loading.value || switching.value || confirming.value) return
  confirming.value = true
  const targetOrganization = organizations.value.find(org => org.key === value)?.name || value
  try {
    await ElMessageBox.confirm(
      t('nav.switchOrganizationNotice', { organization: targetOrganization }),
      t('nav.switchOrganization'),
      {
        confirmButtonText: t('nav.switchOrganizationConfirm'),
        cancelButtonText: t('common.cancel'),
        customClass: 'hfl-message-box--organization-switch',
      },
    )
  } catch {
    return
  } finally {
    confirming.value = false
  }
  try { await switchOrganization(value) }
  catch { notifyError(t('nav.switchOrganizationFailed')) }
}
</script>

<template>
  <div
    v-if="showSwitcher"
    class="org-switcher"
    :class="`org-switcher--${variant}`"
  >
    <ElDropdown
      trigger="click"
      popper-class="nav-dropdown-popover org-switcher-popover"
      :disabled="loading || switching || confirming"
      @command="chooseOrganization"
    >
      <button
        type="button"
        class="org-switcher__trigger"
        :disabled="loading || switching || confirming"
        :aria-label="`${t('nav.orgSwitcher')}: ${currentName}`"
      >
        <Building2
          :size="16"
          aria-hidden="true"
        />
        <span class="org-switcher__name">{{ currentName }}</span>
        <ChevronDown
          :size="14"
          aria-hidden="true"
        />
      </button>
      <template #dropdown>
        <ElDropdownMenu>
          <ElDropdownItem
            v-for="org in organizations"
            :key="org.key"
            :command="org.key"
            :disabled="switching || confirming"
            :class="{ 'is-selected': org.key === currentKey }"
            :aria-current="org.key === currentKey ? 'true' : undefined"
          >
            <span class="org-switcher__option">
              <span class="org-switcher__option-label">{{ org.name }}</span>
            </span>
          </ElDropdownItem>
        </ElDropdownMenu>
      </template>
    </ElDropdown>
  </div>
</template>

<style scoped>
.org-switcher { display: inline-flex; align-items: center; min-width: 0; gap: 6px; }
.org-switcher__trigger { display: inline-flex; align-items: center; box-sizing: border-box; height: 32px; gap: 8px; min-width: 0; max-width: 210px; min-height: 32px; padding: 0 8px; color: var(--tnav-text, #fff); font: inherit; font-size: 13px; line-height: 1.5; border: 1px solid transparent; border-radius: 6px; background: transparent; }
.org-switcher__trigger { cursor: pointer; border-color: var(--tz-border, rgba(255,255,255,.12)); }
.org-switcher__trigger { transition: background-color .15s ease, border-color .15s ease, color .15s ease; }
.org-switcher__trigger:hover, .org-switcher__trigger:focus-visible {
  background: var(--nav-utility-hover-bg, var(--icon-btn-hover-bg, rgba(255,255,255,.08)));
  border-color: var(--nav-utility-border-hover, var(--tz-border, rgba(255,255,255,.12)));
}
.org-switcher--desktop .org-switcher__trigger:hover,
.org-switcher--desktop .org-switcher__trigger:focus-visible {
  color: var(--icon-btn-hover-color, #fff);
}
.org-switcher__trigger:focus-visible { outline: 2px solid var(--el-color-primary); outline-offset: 2px; }
.org-switcher__trigger:disabled { cursor: wait; opacity: .65; }
.org-switcher__name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.org-switcher__option { display: flex; align-items: center; width: 100%; min-width: 0; overflow-wrap: anywhere; }
.org-switcher__option-label { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.org-switcher--mobile { width: 100%; }
.org-switcher--mobile .org-switcher__trigger { color: var(--el-text-color-primary); height: 44px; min-height: 44px; max-width: 100%; }
@media (min-width: 1024px) and (max-width: 1279.98px) { .org-switcher__trigger { max-width: 135px; } }
</style>

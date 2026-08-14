<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ChevronDown, LogOut, User } from 'lucide-vue-next'
import { confirmSignOut, performLogout } from '../lib/logout'
import { useAuth } from '../composables/useAuth'
import { useTheme } from '../composables/useTheme'
import HflPopover from './HflPopover.vue'

const { t } = useI18n()
const router = useRouter()
const { user } = useAuth()
const { theme } = useTheme()
const popoverRef = ref<InstanceType<typeof HflPopover> | null>(null)

const username = computed(() => user.value?.username || '—')
const email = computed(() => user.value?.email || '—')
const role = computed(() => {
  const r = user.value?.access_profile?.role
  if (!r) return '—'
  const roleMap: Record<string, string> = {
    owner: t('account.roleOwner'),
    admin: t('account.roleAdmin'),
    operator: t('account.roleOperator'),
    auditor: t('account.roleAuditor'),
  }
  return roleMap[r] || r
})
const displayLabel = computed(() => username.value)

const profileSub = computed(() => t('account.menuProfileSub').trim())
const signOutSub = computed(() => t('account.menuSignOutSub').trim())

const popperOptions = {
  modifiers: [
    {
      name: 'preventOverflow',
      options: { boundary: 'viewport', padding: 12 },
    },
    { name: 'flip', options: { fallbackPlacements: ['bottom-start', 'top-end'] } },
  ],
}

function go(path: string) {
  popoverRef.value?.hide()
  router.push(path)
}

async function confirmLogout() {
  popoverRef.value?.hide()
  if (!(await confirmSignOut(t))) return
  await performLogout(router)
}
</script>

<template>
  <HflPopover
    ref="popoverRef"
    trigger="click"
    placement="bottom-end"
    :width="320"
    :show-arrow="false"
    :effect="theme === 'light' ? 'light' : 'dark'"
    popper-class="nav-dropdown-popover"
    :popper-options="popperOptions"
    :offset="8"
  >
    <template #reference>
      <button
        type="button"
        class="nav-user-trigger"
        :aria-label="`${displayLabel} · ${t('account.userMenuAria')}`"
      >
        <span class="nav-user-trigger__label">{{ displayLabel }}</span>
        <ChevronDown
          :size="14"
          class="nav-user-trigger__caret"
          stroke-width="2"
          aria-hidden="true"
        />
      </button>
    </template>

    <div class="nav-dropdown-panel">
      <header class="nav-dropdown-panel__head nav-dropdown-panel__head--stacked">
        <h3 class="nav-dropdown-panel__title">
          {{ email }}
        </h3>
        <span class="nav-dropdown-panel__role-badge">{{ role }}</span>
      </header>

      <div class="nav-dropdown-panel__body">
        <button
          type="button"
          class="nav-dropdown-panel__item"
          @click="go('/account/profile')"
        >
          <span
            class="nav-dropdown-panel__icon-box"
            aria-hidden="true"
          >
            <User
              :size="16"
              stroke-width="1.75"
            />
          </span>
          <span class="nav-dropdown-panel__item-text">
            <span class="nav-dropdown-panel__item-title">{{ t('account.menuProfile') }}</span>
            <span
              v-if="profileSub"
              class="nav-dropdown-panel__item-sub"
            >{{ profileSub }}</span>
          </span>
        </button>
      </div>

      <div class="nav-dropdown-panel__divider" />

      <div class="nav-dropdown-panel__body">
        <button
          type="button"
          class="nav-dropdown-panel__item nav-dropdown-panel__item--danger"
          @click.stop.prevent="confirmLogout"
        >
          <span
            class="nav-dropdown-panel__icon-box nav-dropdown-panel__icon-box--danger"
            aria-hidden="true"
          >
            <LogOut
              :size="16"
              stroke-width="1.75"
            />
          </span>
          <span class="nav-dropdown-panel__item-text">
            <span class="nav-dropdown-panel__item-title">{{ t('account.menuSignOut') }}</span>
            <span
              v-if="signOutSub"
              class="nav-dropdown-panel__item-sub"
            >{{ signOutSub }}</span>
          </span>
        </button>
      </div>
    </div>
  </HflPopover>
</template>

<style scoped>
.nav-user-trigger {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 32px;
  padding: 0 10px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: var(--nav-user-trigger-color, rgba(255, 255, 255, 0.88));
  cursor: pointer;
  transition:
    background-color 0.15s ease,
    color 0.15s ease;
}

.nav-user-trigger:hover {
  background: var(--nav-user-trigger-hover-bg, rgba(255, 255, 255, 0.08));
  color: var(--nav-user-trigger-hover-color, #fff);
}

.nav-user-trigger__label {
  max-width: 140px;
  overflow: hidden;
  font-size: 14px;
  font-weight: 500;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.nav-user-trigger__caret {
  flex-shrink: 0;
  margin-left: -2px;
  color: var(--nav-user-trigger-caret-color, rgba(255, 255, 255, 0.72));
}

.nav-user-trigger:hover .nav-user-trigger__caret {
  color: var(--nav-user-trigger-caret-hover-color, rgba(255, 255, 255, 0.95));
}

@media (max-width: 1023.98px) {
  .nav-user-trigger {
    min-height: 44px;
  }
}

@media (min-width: 1024px) and (max-width: 1151.98px) {
  .nav-user-trigger {
    padding-right: 8px;
    padding-left: 8px;
  }

  .nav-user-trigger__label {
    max-width: 72px;
  }
}

@media (max-width: 479.98px) {
  .nav-user-trigger {
    width: 44px;
    height: 44px;
    justify-content: center;
    padding: 0;
  }

  .nav-user-trigger__label {
    max-width: 32px;
    font-size: 12px;
  }

  .nav-user-trigger__caret {
    display: none;
  }
}
</style>

<style>
:root[data-theme="light"] .nav-user-trigger__caret {
  color: rgba(0, 0, 0, 0.55);
}

:root[data-theme="light"] .nav-user-trigger:hover .nav-user-trigger__caret {
  color: rgba(0, 0, 0, 0.75);
}
</style>

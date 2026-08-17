<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ChevronDown, LogOut, User } from 'lucide-vue-next'
import { confirmSignOut, performLogout } from '../lib/logout'
import { useAuth } from '../composables/useAuth'
import { fetchDeployProfile } from '../composables/useDeployProfile'
import { useTheme } from '../composables/useTheme'
import HflPopover from './HflPopover.vue'

const { t } = useI18n()
const router = useRouter()
const { user } = useAuth()
const { theme } = useTheme()
const popoverRef = ref<InstanceType<typeof HflPopover> | null>(null)
const productProfileLoaded = ref(false)
const productVersion = ref<string | null>(null)
const productEdition = ref<'community' | 'enterprise'>('community')

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
const editionLabel = computed(() => (
  productEdition.value === 'enterprise'
    ? t('account.editionEnterprise')
    : t('account.editionCommunity')
))
const productIdentity = computed(() => {
  const productName = productVersion.value
    ? `HyperFileLens v${productVersion.value}`
    : 'HyperFileLens'
  const details = productVersion.value
    ? [editionLabel.value]
    : [t('account.developmentBuild'), editionLabel.value]
  return [productName, ...details].join(
    ` ${t('common.dotSeparator')} `,
  )
})

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

onMounted(async () => {
  const profile = await fetchDeployProfile()
  // Older backends may return a profile without the product identity fields.
  // Keep the footer hidden instead of mislabeling that instance as a dev build.
  if (
    !profile
    || profile.product_version === undefined
    || profile.edition === undefined
  ) return
  productVersion.value = profile.product_version?.trim() || null
  productEdition.value = profile.edition === 'enterprise' ? 'enterprise' : 'community'
  productProfileLoaded.value = true
})

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

      <template v-if="productProfileLoaded">
        <div class="nav-dropdown-panel__divider" />
        <footer
          class="nav-user-product"
          :aria-label="t('account.productInfoAria')"
        >
          <span class="nav-user-product__name">{{ productIdentity }}</span>
        </footer>
      </template>
    </div>
  </HflPopover>
</template>

<style scoped>
.nav-user-trigger {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  flex: 0 1 auto;
  align-items: center;
  box-sizing: border-box;
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
  min-width: 0;
  max-width: 140px;
  flex: 1 1 auto;
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

.nav-user-product {
  min-width: 0;
  padding: 9px 12px 10px;
  color: var(--color-text-tertiary, #909399);
  font-size: 12px;
  line-height: 1.4;
  user-select: text;
}

.nav-user-product__name {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
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

<script setup lang="ts">
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { Menu, Shield } from 'lucide-vue-next'
import NavNotificationPopover from '../../components/NavNotificationPopover.vue'
import NavUserMenu from '../../components/NavUserMenu.vue'
import OrgSwitcher from '../../components/OrgSwitcher.vue'
import { beginRouteRequestScope } from '../../lib/routeRequestAbort'
import { beginRouteTransition } from '../../lib/routeTransition'
import { useAppPrimaryNav } from '../../composables/useAppPrimaryNav'
import LanguageSwitcher from '../../components/LanguageSwitcher.vue'

withDefaults(
  defineProps<{
    mobileMenuOpen?: boolean
    adminConsoleHref?: string
    timezoneDisplay?: string
    timezoneOffsetDisplay?: string
  }>(),
  {
    adminConsoleHref: '',
    timezoneDisplay: '',
    timezoneOffsetDisplay: '',
  },
)

const emit = defineEmits<{
  'toggle-mobile-menu': []
}>()

const { t } = useI18n()

const route = useRoute()
const router = useRouter()
const { items: navItems, isActive: navActive } = useAppPrimaryNav()

function navigateImmediately(to: string) {
  if (to === route.fullPath) return
  beginRouteTransition()
  beginRouteRequestScope()
  void router.push(to)
}

function handleNavClick(event: MouseEvent, to: string) {
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  navigateImmediately(to)
}
</script>

<template>
  <header class="top-nav">
    <button
      type="button"
      class="mobile-menu-button"
      :aria-label="t('nav.openMenu')"
      :aria-expanded="mobileMenuOpen || false"
      @click="emit('toggle-mobile-menu')"
    >
      <Menu
        :size="21"
        aria-hidden="true"
      />
    </button>

    <button
      type="button"
      class="logo"
      :aria-label="t('nav.overview')"
      @click="navigateImmediately('/')"
    >
      <img
        class="logo-lockup"
        src="/brand/images/hyperfilelens-lockup-on-dark.png"
        alt="HyperFileLens"
      >
    </button>

    <nav class="nav-menu">
      <RouterLink
        v-for="item in navItems"
        :key="item.to"
        :to="item.to"
        :class="{ active: navActive(item.to) }"
        class="nav-item"
        @click="handleNavClick($event, item.to)"
      >
        <component
          :is="item.icon"
          class="nav-item__icon"
          :size="16"
          :stroke-width="2"
          aria-hidden="true"
        />
        <span>{{ item.label }}</span>
      </RouterLink>
    </nav>

    <div class="right-menu">
      <span
        class="timezone-display desktop-navigation-control"
        :title="timezoneDisplay"
      >
        <span class="timezone-display__label">{{ t('nav.timezone') }}</span>
        <span>{{ timezoneOffsetDisplay }}</span>
      </span>

      <span class="desktop-navigation-control"><OrgSwitcher /></span>

      <a
        v-if="adminConsoleHref"
        class="platform-ops-entry desktop-navigation-control"
        :href="adminConsoleHref"
        :title="t('nav.platformOps')"
        :aria-label="t('nav.platformOps')"
        target="_blank"
        rel="noopener noreferrer"
      >
        <Shield
          :size="15"
          aria-hidden="true"
        />
        <span>{{ t('nav.platformOps') }}</span>
      </a>

      <div class="alerts-btn">
        <NavNotificationPopover />
      </div>

      <span class="desktop-navigation-control">
        <LanguageSwitcher variant="navigation" />
      </span>

      <NavUserMenu />
    </div>
  </header>
</template>

<style scoped>
.top-nav {
  --topnav-height: var(--app-header-height);
  --brand-color: var(--color-primary, #6D5EF6);
  --brand-glow: rgba(109, 94, 246, 0.5);
  --nav-item-active-color: var(--color-primary, #6D5EF6);

  height: var(--topnav-height);
  background:
    var(--tnav-overlay, linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0))),
    var(--tnav-bg, #0F0F17);
  border-bottom: 1px solid var(--tnav-border, rgba(109, 94, 246, 0.24));
  position: sticky;
  top: 0;
  z-index: 40;
  display: flex;
  align-items: center;
  width: 100%;
  min-width: 0;
  box-sizing: border-box;
  padding: var(--app-safe-top) max(16px, var(--app-safe-right)) 0 max(12px, var(--app-safe-left));
  box-shadow: var(--tnav-shadow, 0 12px 26px rgba(9, 8, 15, 0.22));
  font-family: var(--font-sans);
}

.logo {
  display: flex;
  align-items: center;
  width: 142px;
  height: 100%;
  cursor: pointer;
  padding: 0;
  border: 0;
  background: transparent;
}

.logo-lockup {
  display: block;
  width: 128px;
  height: auto;
  max-width: 100%;
  object-fit: contain;
  transform: translateY(2px);
}

.mobile-menu-button {
  display: none;
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  align-items: center;
  justify-content: center;
  margin-right: 4px;
  padding: 0;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: var(--icon-btn-color, #aeb2c5);
  cursor: pointer;
}

.mobile-menu-button:hover,
.mobile-menu-button:focus-visible {
  background: var(--icon-btn-hover-bg, rgba(255, 255, 255, 0.08));
  color: var(--icon-btn-hover-color, #fff);
}

.nav-menu {
  display: flex;
  min-width: 0;
  flex: 0 1 auto;
  align-items: center;
  height: 52px;
}

.nav-item {
  position: relative;
  height: 52px;
  min-width: 90px;
  padding: 0 16px;
  font-size: 13px;
  font-weight: 650;
  color: #a1a1aa;
  text-decoration: none;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border-radius: 8px 8px 0 0;
  transition: color 150ms ease, background-color 150ms ease;
}

.nav-item__icon {
  flex: 0 0 auto;
}

.nav-item:hover {
  color: var(--nav-item-hover-color, #E2E2E2);
  background-color: var(--nav-item-hover-bg, rgba(255, 255, 255, 0.05));
}

.nav-item.active {
  color: #f8fafc;
  background: rgba(109, 94, 246, 0.1);
}

.nav-item.active::before {
  content: '';
  position: absolute;
  left: 18px;
  right: 18px;
  bottom: -1px;
  height: 18px;
  background: radial-gradient(ellipse at center, rgba(109, 94, 246, 0.42), transparent 68%);
  pointer-events: none;
}

.nav-item.active::after {
  content: '';
  position: absolute;
  left: 16px;
  right: 16px;
  bottom: 6px;
  width: auto;
  height: 2px;
  transform: none;
  --tw-gradient-from: var(--nav-active-accent, #F5A623);
  --tw-gradient-to: var(--nav-active-accent, #F5A623);
  --tw-gradient-position: to right in oklab;
  background-image: linear-gradient(var(--tw-gradient-position), var(--tw-gradient-from), var(--tw-gradient-to));
  box-shadow: var(--nav-active-line-shadow, 0 -2px 12px rgba(245, 166, 35, 0.35));
}

.right-menu {
  min-width: 0;
  flex: 0 1 auto;
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 4px;
}

.timezone-display {
  height: 32px;
  padding: 0 10px;
  margin-right: 12px;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.5;
  white-space: nowrap;
  color: var(--tz-color, #c9cdd4);
  display: flex;
  align-items: center;
  gap: 4px;
  border-radius: 10px;
  background: var(--tz-bg, rgba(255, 255, 255, 0.06));
  border: 1px solid var(--tz-border, rgba(255, 255, 255, 0.12));
}

.alerts-btn {
  position: relative;
}

@media (min-width: 1024px) and (max-width: 1439.98px) {
  .top-nav {
    padding-right: max(8px, var(--app-safe-right));
    padding-left: max(8px, var(--app-safe-left));
  }

  .logo {
    width: 140px;
    flex: 0 0 140px;
  }

  .nav-menu {
    min-width: 0;
  }

  .nav-item__icon {
    display: none;
  }

  .nav-item {
    min-width: 0;
    padding-right: 8px;
    padding-left: 8px;
    white-space: nowrap;
  }

  .timezone-display {
    margin-right: 0;
    padding-right: 8px;
    padding-left: 8px;
  }

  .top-nav .platform-ops-entry {
    width: 32px;
    height: 32px;
    box-sizing: border-box;
    justify-content: center;
    padding: 0;
    border: 0;
    border-radius: 6px;
    background: transparent;
    color: var(--icon-btn-color, #aeb2c5);
  }

  .platform-ops-entry span {
    display: none;
  }

  .top-nav .platform-ops-entry svg {
    width: 18px;
    height: 18px;
  }

  .top-nav .platform-ops-entry:hover,
  .top-nav .platform-ops-entry:focus-visible {
    border-color: transparent;
    background: var(--icon-btn-hover-bg, rgba(255, 255, 255, 0.08));
    color: var(--icon-btn-hover-color, #E2E2E2);
  }

  .top-nav .platform-ops-entry:focus-visible {
    outline: 2px solid var(--color-primary, #6D5EF6);
    outline-offset: 2px;
  }

  .timezone-display__label {
    display: none;
  }
}

@media (max-width: 1023.98px) {
  .top-nav {
    padding-right: max(8px, var(--app-safe-right));
    padding-left: max(6px, var(--app-safe-left));
  }

  .mobile-menu-button {
    display: inline-flex;
  }

  .logo {
    width: auto;
    min-width: 0;
  }

  .nav-menu,
  .top-nav .desktop-navigation-control {
    display: none;
  }

  .right-menu {
    min-width: 0;
  }

  .alerts-btn {
    width: 44px;
    height: 44px;
  }
}

@media (max-width: 479.98px) {
  .logo {
    width: 124px;
  }

  .logo-lockup {
    width: 114px;
  }
}

.theme-options {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.theme-options-title {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  padding: 0 4px 4px;
  border-bottom: 1px solid var(--el-border-color-lighter);
  margin-bottom: 4px;
}

.theme-option {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
  transition: background-color 150ms ease;
  position: relative;
}

.theme-option:hover {
  background-color: var(--el-fill-color-light);
}

.theme-option.active {
  background-color: var(--el-fill-color);
}

.theme-preview {
  width: 80px;
  height: 48px;
  border-radius: 4px;
  overflow: hidden;
  display: flex;
  border: 1px solid var(--el-border-color);
}

.preview-sidebar {
  width: 16px;
}

.preview-content {
  flex: 1;
  padding: 4px;
  display: flex;
  flex-direction: column;
  gap: 3px;
}

.preview-header {
  height: 8px;
  border-radius: 2px;
}

.preview-body {
  flex: 1;
  border-radius: 2px;
}

.theme-option-info {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--el-text-color-primary);
}

.theme-check {
  position: absolute;
  right: 8px;
  color: var(--el-color-primary);
}

.preview-light {
  background: #F5F5F5;
}
.preview-light .preview-sidebar {
  background: #E8E8E8;
}
.preview-light .preview-header {
  background: #DDD;
}
.preview-light .preview-body {
  background: #EEE;
}

.preview-hybrid {
  background: #F5F5F5;
  position: relative;
  padding-top: 10px;
}
.preview-hybrid .preview-sidebar {
  position: absolute;
  left: 0;
  top: 10px;
  bottom: 0;
  width: 16px;
  background: #2C2C2E;
}
.preview-hybrid .preview-header {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 10px;
  background: #2C2C2E;
}
.preview-hybrid .preview-content {
  margin-left: 16px;
  padding: 4px;
  flex: 1;
  background: #F5F5F5;
}

.preview-dark {
  background: #1C1C1E;
}
.preview-dark .preview-sidebar {
  background: #2C2C2E;
}
.preview-dark .preview-header {
  background: #3A3A3C;
}
.preview-dark .preview-body {
  background: #2C2C2E;
}

.platform-ops-entry {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 8px;
  color: var(--nav-item-color, #bec2d4);
  font-size: 13px;
  font-weight: 500;
  line-height: 1.5;
  text-decoration: none;
  white-space: nowrap;
}

.platform-ops-entry:hover {
  border-color: var(--color-primary, #6D5EF6);
  color: var(--nav-item-hover-color, #fff);
}

</style>

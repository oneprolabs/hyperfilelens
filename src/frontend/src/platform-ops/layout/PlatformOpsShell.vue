<script setup lang="ts">
import { computed, ref, onMounted, watch, nextTick } from 'vue'
import { useRoute, RouterView } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft, Menu } from 'lucide-vue-next'
import NavUserMenu from '../../components/NavUserMenu.vue'
import Sidebar from '../../components/Sidebar.vue'
import { useTheme } from '../../composables/useTheme'
import { fetchDeployProfile } from '../../composables/useDeployProfile'
import { applyThemeVars } from '../composables/applyThemeVars'
import { useResolvedPlatformOpsSideNav } from '../composables/useResolvedPlatformOpsSideNav'
import { platformOpsRouteViewKey } from '../lib/platformOpsRouteViewKey'
import MobileNavigationDrawer from '../../components/MobileNavigationDrawer.vue'
import '../styles/platform-ops-ui.css'
import '../styles/monitoring.css'
import '../styles/accountManagement.css'

const { t } = useI18n()
const route = useRoute()
const { theme } = useTheme()

const tenantUrl = ref('')
const themeVersion = ref(0)
const fallbackSidebarCollapsed = ref(localStorage.getItem('sidebar-collapsed') === 'true')
const fallbackMenuItems = useResolvedPlatformOpsSideNav()
const mobileNavigationOpen = ref(false)

const routeSkeletonShowsCards = computed(
  () => route.path.startsWith('/platform-ops/monitoring')
    || route.path.startsWith('/platform-ops/alert-center')
    || route.path.startsWith('/platform-ops/audit-center')
    || route.path.startsWith('/platform-ops/platform/integrations')
    || route.path === '/platform-ops/overview'
    || route.path === '/platform-ops/users'
    || route.path === '/platform-ops/orgs',
)

onMounted(async () => {
  const profile = await fetchDeployProfile()
  if (profile?.tenant_public_url) {
    try {
      tenantUrl.value = new URL(profile.tenant_public_url).toString()
    } catch {
      tenantUrl.value = ''
    }
  }
})

watch(
  theme,
  async (mode) => {
    applyThemeVars(mode)
    themeVersion.value++
    await nextTick()
    themeVersion.value++
  },
  { immediate: true },
)

function goTenantConsole() {
  if (!tenantUrl.value) return
  window.location.assign(tenantUrl.value)
}

function toggleFallbackSidebar() {
  fallbackSidebarCollapsed.value = !fallbackSidebarCollapsed.value
  localStorage.setItem('sidebar-collapsed', String(fallbackSidebarCollapsed.value))
}

watch(
  () => route.path,
  () => {
    fallbackSidebarCollapsed.value = localStorage.getItem('sidebar-collapsed') === 'true'
    mobileNavigationOpen.value = false
  },
)
</script>

<template>
  <div
    class="platform-ops-shell"
    :data-theme-version="themeVersion"
    :style="{ backgroundColor: 'var(--content-bg)', '--topnav-height': 'var(--app-header-height)' }"
  >
    <header class="platform-ops-header">
      <button
        type="button"
        class="platform-ops-header__menu"
        :aria-label="t('nav.openMenu')"
        :aria-expanded="mobileNavigationOpen"
        @click="mobileNavigationOpen = !mobileNavigationOpen"
      >
        <Menu
          :size="21"
          aria-hidden="true"
        />
      </button>
      <div class="platform-ops-header__brand">
        <img
          class="platform-ops-header__lockup"
          src="/brand/images/hyperfilelens-lockup-on-dark.png"
          alt="HyperFileLens"
        >
        <i aria-hidden="true" />
        <span class="platform-ops-header__product">{{ t('platformOps.nav.title') }}</span>
      </div>
      <div class="platform-ops-header__actions">
        <button
          type="button"
          class="platform-ops-return"
          :aria-label="t('platformOps.nav.backToConsole')"
          :disabled="!tenantUrl"
          @click="goTenantConsole"
        >
          <ArrowLeft
            :size="16"
            aria-hidden="true"
          />
          <span>{{ t('platformOps.nav.backToConsole') }}</span>
        </button>
        <NavUserMenu />
      </div>
    </header>
    <MobileNavigationDrawer
      v-model="mobileNavigationOpen"
      :title="t('platformOps.nav.title')"
      :module-items="fallbackMenuItems"
    />
    <main class="platform-ops-main">
      <RouterView v-slot="{ Component, route: viewRoute }">
        <Suspense
          :key="platformOpsRouteViewKey(viewRoute)"
          timeout="0"
        >
          <component :is="Component" />
          <template #fallback>
            <div
              class="platform-ops-main__route-fallback"
              :class="{ 'platform-ops-main__route-fallback--collapsed': fallbackSidebarCollapsed }"
              role="status"
              aria-label="Loading"
            >
              <div class="platform-ops-main__route-fallback-side">
                <Sidebar
                  :collapsed="fallbackSidebarCollapsed"
                  :menus="fallbackMenuItems"
                  @toggle="toggleFallbackSidebar"
                />
              </div>
              <section class="platform-ops-main__route-fallback-content">
                <div
                  class="platform-ops-route-skeleton"
                  :class="{ 'platform-ops-route-skeleton--with-cards': routeSkeletonShowsCards }"
                  aria-hidden="true"
                >
                  <div class="platform-ops-route-skeleton__header">
                    <span class="platform-ops-route-skeleton__title" />
                    <div class="platform-ops-route-skeleton__actions">
                      <span />
                      <span />
                    </div>
                  </div>
                  <div
                    v-if="routeSkeletonShowsCards"
                    class="platform-ops-route-skeleton__cards"
                  >
                    <span
                      v-for="idx in 4"
                      :key="`ops-card-${idx}`"
                      class="platform-ops-route-skeleton__card"
                    >
                      <i />
                      <b />
                      <em />
                    </span>
                  </div>
                  <div class="platform-ops-route-skeleton__panel">
                    <div class="platform-ops-route-skeleton__toolbar">
                      <span />
                      <i />
                    </div>
                    <div class="platform-ops-route-skeleton__rows">
                      <span
                        v-for="idx in 8"
                        :key="`ops-row-${idx}`"
                      >
                        <i />
                        <i />
                        <i />
                        <i />
                      </span>
                    </div>
                  </div>
                </div>
              </section>
            </div>
          </template>
        </Suspense>
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.platform-ops-shell {
  min-height: var(--app-viewport-height);
  color: var(--page-text, #1c1c26);
  font-family: var(--font-sans);
}

.platform-ops-header {
  height: var(--topnav-height, 52px);
  display: flex;
  align-items: center;
  justify-content: space-between;
  box-sizing: border-box;
  padding: var(--app-safe-top) max(16px, var(--app-safe-right)) 0 max(12px, var(--app-safe-left));
  border-bottom: 1px solid var(--tnav-border, rgba(109, 94, 246, 0.24));
  background:
    var(--tnav-overlay, linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0))),
    var(--tnav-bg, #0f0f17);
  box-shadow: var(--tnav-shadow, 0 12px 26px rgba(9, 8, 15, 0.22));
  position: sticky;
  top: 0;
  z-index: 40;
}

.platform-ops-header__menu {
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

.platform-ops-header__menu:hover,
.platform-ops-header__menu:focus-visible {
  background: var(--icon-btn-hover-bg, rgba(255, 255, 255, 0.08));
  color: var(--icon-btn-hover-color, #fff);
}

.platform-ops-header__brand {
  display: flex;
  align-items: center;
  gap: 9px;
  color: var(--tnav-text, #f8fafc);
  white-space: nowrap;
}

.platform-ops-header__lockup {
  display: block;
  width: 128px;
  height: auto;
  max-width: 100%;
  object-fit: contain;
  transform: translateY(2px);
}

.platform-ops-header__brand > i {
  width: 1px;
  height: 16px;
  margin: 0 2px;
  background: rgba(248, 250, 252, 0.24);
}

.platform-ops-header__product {
  color: var(--nav-item-color, #bec2d4);
  font-size: 13px;
  font-weight: 600;
}

.platform-ops-header__actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.platform-ops-return {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 6px 10px;
  border-radius: 8px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  background: transparent;
  color: var(--nav-item-color, #bec2d4);
  cursor: pointer;
  font-size: 13px;
  font-weight: 500;
  line-height: 1.5;
  white-space: nowrap;
}

.platform-ops-return:hover {
  border-color: var(--color-primary, #6d5ef6);
  color: var(--nav-item-hover-color, #fff);
}

.platform-ops-main {
  position: relative;
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
}

.platform-ops-main__route-fallback {
  display: grid;
  grid-template-columns: 238px minmax(0, 1fr);
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
  background: var(--content-bg, #f4f4f7);
}

.platform-ops-main__route-fallback--collapsed {
  grid-template-columns: 56px minmax(0, 1fr);
}

.platform-ops-main__route-fallback-side {
  position: sticky;
  top: var(--topnav-height, 52px);
  display: flex;
  height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
  min-width: 0;
  flex-shrink: 0;
}

.platform-ops-main__route-fallback-content {
  display: block;
  min-width: 0;
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
  padding: 20px 24px 28px;
  overflow: hidden;
}

.platform-ops-route-skeleton {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)) - 48px);
  animation: platform-ops-route-skeleton-fade 180ms ease-out;
}

.platform-ops-route-skeleton__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.platform-ops-route-skeleton__title,
.platform-ops-route-skeleton__actions span,
.platform-ops-route-skeleton__card,
.platform-ops-route-skeleton__toolbar span,
.platform-ops-route-skeleton__toolbar i,
.platform-ops-route-skeleton__rows i {
  position: relative;
  overflow: hidden;
  background: var(--color-card-bg, #fff);
}

.platform-ops-route-skeleton__title::after,
.platform-ops-route-skeleton__actions span::after,
.platform-ops-route-skeleton__card::after,
.platform-ops-route-skeleton__toolbar span::after,
.platform-ops-route-skeleton__toolbar i::after,
.platform-ops-route-skeleton__rows i::after {
  content: '';
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(109, 94, 246, 0.1), transparent);
  animation: platform-ops-route-skeleton-shimmer 1.15s ease-in-out infinite;
}

.platform-ops-route-skeleton__title {
  display: block;
  width: 220px;
  height: 28px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 6px;
}

.platform-ops-route-skeleton__actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}

.platform-ops-route-skeleton__actions span {
  display: block;
  width: 92px;
  height: 34px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.platform-ops-route-skeleton__cards {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.platform-ops-route-skeleton__card {
  display: grid;
  gap: 12px;
  min-height: 112px;
  padding: 16px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  box-shadow: 0 10px 24px rgba(28, 28, 38, 0.03);
}

.platform-ops-route-skeleton__card i,
.platform-ops-route-skeleton__card b,
.platform-ops-route-skeleton__card em {
  display: block;
  height: 12px;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.platform-ops-route-skeleton__card i {
  width: 42%;
}

.platform-ops-route-skeleton__card b {
  width: 68%;
}

.platform-ops-route-skeleton__card em {
  width: 100%;
}

.platform-ops-route-skeleton__panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
  padding: 14px 16px 18px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
  box-shadow: 0 12px 28px rgba(28, 28, 38, 0.04);
}

.platform-ops-route-skeleton__toolbar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--color-border-light, #f2f2f6);
}

.platform-ops-route-skeleton__toolbar span {
  width: 260px;
  height: 32px;
  border-radius: 8px;
  background: var(--color-grey-2, #f5f5f7);
}

.platform-ops-route-skeleton__toolbar i {
  width: 132px;
  height: 32px;
  border-radius: 8px;
  background: var(--color-grey-2, #f5f5f7);
}

.platform-ops-route-skeleton__rows {
  display: grid;
  flex: 1;
  grid-template-rows: repeat(8, minmax(34px, 1fr));
  gap: 12px;
  min-height: 0;
  padding-top: 16px;
}

.platform-ops-route-skeleton__rows span {
  display: grid;
  grid-template-columns: 1.4fr 1fr 0.8fr 0.9fr;
  gap: 18px;
  align-items: center;
  min-height: 0;
}

.platform-ops-route-skeleton__rows i {
  display: block;
  height: 12px;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

@keyframes platform-ops-route-skeleton-shimmer {
  100% {
    transform: translateX(100%);
  }
}

@keyframes platform-ops-route-skeleton-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

@media (max-width: 1023.98px) {
  .platform-ops-header {
    justify-content: flex-start;
    padding-right: max(8px, var(--app-safe-right));
    padding-left: max(6px, var(--app-safe-left));
  }

  .platform-ops-header__menu {
    display: inline-flex;
  }

  .platform-ops-header__brand {
    min-width: 0;
    overflow: hidden;
  }

  .platform-ops-header__brand span {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .platform-ops-header__lockup {
    width: 114px;
  }

  .platform-ops-header__actions {
    min-width: 0;
    margin-left: auto;
  }

  .platform-ops-return {
    display: none;
  }

  .platform-ops-main__route-fallback,
  .platform-ops-main__route-fallback--collapsed {
    grid-template-columns: minmax(0, 1fr);
  }

  .platform-ops-main__route-fallback-side {
    display: none;
  }

  .platform-ops-main__route-fallback-content {
    padding: 12px;
  }
}

@media (max-width: 960px) {
  .platform-ops-route-skeleton__cards {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .platform-ops-route-skeleton__rows span {
    grid-template-columns: 1.4fr 1fr;
  }

  .platform-ops-route-skeleton__rows i:nth-child(n + 3) {
    display: none;
  }
}

@media (max-width: 640px) {
  .platform-ops-header {
    padding-right: 8px;
  }

  .platform-ops-return {
    width: 34px;
    padding: 0;
    justify-content: center;
  }

  .platform-ops-return span {
    display: none;
  }
}
</style>

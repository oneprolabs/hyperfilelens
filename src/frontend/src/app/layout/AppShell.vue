<script setup lang="ts">
import { computed, onMounted, ref, watch, nextTick } from 'vue'
import TopNav from './TopNav.vue'
import { RouterView, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { LANG_STORAGE_KEY } from '../../i18n'
import { useTheme } from '../../composables/useTheme'
import { platformOpsEntryUrl, fetchDeployProfile } from '../../composables/useDeployProfile'
import { currentUser } from '../../composables/useAuth'
import { useOrganizationSwitcher } from '../../composables/useOrganizationSwitcher'
import Sidebar from '../../components/Sidebar.vue'
import type { MenuItem } from '../../components/ModulePage.vue'
import { useInsightSideNav } from '../../composables/useInsightSideNav'
import { useNodeSideNav } from '../../composables/useNodeSideNav'
import { useOpsMenus } from '../../composables/useOpsMenus'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useAccountCenterMenus } from '../../composables/useAccountCenterMenus'
import MobileNavigationDrawer from '../../components/MobileNavigationDrawer.vue'
import { useAppPrimaryNav } from '../../composables/useAppPrimaryNav'

const { locale, t } = useI18n()
const route = useRoute()
const { theme } = useTheme()
const supportOrgKey = ref<string | null>(null)
const adminConsoleUrl = ref('')
const adminConsoleLandingPath = ref('')
// Bump this whenever theme changes so child components re-evaluate CSS var refs
const themeVersion = ref(0)
const fallbackSidebarCollapsed = ref(localStorage.getItem('sidebar-collapsed') === 'true')
const protectionFallbackMenus = useProtectionSideNav()
const insightFallbackMenus = useInsightSideNav()
const nodeFallbackMenus = useNodeSideNav()
const opsFallbackMenus = useOpsMenus()
const accountFallbackMenus = useAccountCenterMenus()
const mobileNavigationOpen = ref(false)
const { itemsWithActiveState: primaryNavItems } = useAppPrimaryNav()
const { showSwitcher: showOrganizationSwitcher } = useOrganizationSwitcher()

const adminConsoleHref = computed(() =>
  platformOpsEntryUrl(adminConsoleUrl.value, adminConsoleLandingPath.value || undefined),
)
const timezoneOffsetDisplay = computed(() => {
  const offset = new Date().getTimezoneOffset()
  const sign = offset <= 0 ? '+' : '-'
  const absOffset = Math.abs(offset)
  const hours = String(Math.floor(absOffset / 60)).padStart(2, '0')
  const minutes = String(absOffset % 60).padStart(2, '0')
  return `GMT${sign}${hours}:${minutes}`
})
const timezoneDisplay = computed(() => `${t('nav.timezone')}${timezoneOffsetDisplay.value}`)

function pathMatchesPrefix(path: string, prefix: string) {
  return path === prefix || path.startsWith(`${prefix}/`)
}

const fallbackMenuItems = computed<MenuItem[]>(() => {
  const path = route.path
  if (
    pathMatchesPrefix(path, '/protection') ||
    pathMatchesPrefix(path, '/node/agents') ||
    pathMatchesPrefix(path, '/node/repositories') ||
    pathMatchesPrefix(path, '/node/snapshots') ||
    pathMatchesPrefix(path, '/node/deployment')
  ) {
    return protectionFallbackMenus.value
  }
  if (
    pathMatchesPrefix(path, '/insight') ||
    pathMatchesPrefix(path, '/node/ai-settings') ||
    pathMatchesPrefix(path, '/node/knowledge-base') ||
    pathMatchesPrefix(path, '/node/gateways')
  ) {
    return insightFallbackMenus.value
  }
  if (pathMatchesPrefix(path, '/ops')) return opsFallbackMenus.value
  if (pathMatchesPrefix(path, '/account')) return accountFallbackMenus.value
  if (pathMatchesPrefix(path, '/node')) return nodeFallbackMenus.value
  return []
})

const fallbackIsBackupFlow = computed(() => route.path === '/protection/backups')
const fallbackIsDashboard = computed(() => route.path === '/')
const fallbackIsHostMonitor = computed(() => route.path === '/ops/host-monitor')
const fallbackIsCopilot = computed(() => route.path === '/insight/copilot')
const fallbackIsAccountProfile = computed(() => route.path === '/account/profile')
const fallbackIsNodeSubscription = computed(() => route.path === '/node/subscription')
const fallbackIsNodeSystem = computed(() => route.path === '/node/system')
const fallbackIsFullscreen = computed(() => route.meta.layout === 'fullscreen')
const fallbackHasSidebar = computed(() => !fallbackIsFullscreen.value && fallbackMenuItems.value.length > 0)

function toggleFallbackSidebar() {
  fallbackSidebarCollapsed.value = !fallbackSidebarCollapsed.value
  localStorage.setItem('sidebar-collapsed', String(fallbackSidebarCollapsed.value))
}

async function refreshHeaderProfile() {
  const profile = await fetchDeployProfile(true)
  supportOrgKey.value = profile?.support_org_key ?? null
  adminConsoleUrl.value = profile?.admin_console_entry_visible
    ? profile.admin_console_url
    : ''
  adminConsoleLandingPath.value = profile?.admin_console_entry_visible
    ? (profile.admin_console_landing_path || '')
    : ''
}

onMounted(() => {
  void refreshHeaderProfile()
})

watch(
  () => currentUser.value?.id,
  () => {
    void refreshHeaderProfile()
  },
)

watch(
  locale,
  (v) => {
    try {
      localStorage.setItem(LANG_STORAGE_KEY, v)
      document.documentElement.lang = v
    } catch {
      /* ignore */
    }
  },
  { flush: 'post', immediate: true },
)

watch(
  () => route.fullPath,
  () => {
    mobileNavigationOpen.value = false
  },
)

// Apply theme CSS variables to document root
watch(
  theme,
  async (t) => {
    const root = document.documentElement
    root.setAttribute('data-theme', t)
    // Toggle .dark class for Element Plus dark mode
    if (t === 'dark') {
      root.classList.add('dark')
      root.style.setProperty('--lightningcss-dark', 'dark')
    } else {
      root.classList.remove('dark')
      root.style.setProperty('--lightningcss-dark', '')
    }
    applyThemeVars(t)
    // Bump version so child components re-evaluate CSS var references
    themeVersion.value++
    await nextTick()
    // Another tick for deeply mounted components
    themeVersion.value++
  },
  { immediate: true },
)

function applyThemeVars(t: string) {
  const root = document.documentElement
  if (t === 'dark') {
    root.style.setProperty('--color-primary', '#6D5EF6')
    root.style.setProperty('--color-primary-gradient-start', '#7E6CEF')
    root.style.setProperty('--color-primary-gradient-end', '#6D5EF6')
    root.style.setProperty('--color-primary-hover-gradient-start', '#8876F5')
    root.style.setProperty('--color-primary-hover-gradient-end', '#7664FA')
    root.style.setProperty('--color-primary-active-gradient-start', '#5F50E0')
    root.style.setProperty('--color-primary-active-gradient-end', '#5546D8')
    root.style.setProperty('--color-primary-hover', '#5546D8')
    root.style.setProperty('--color-primary-active', '#4536BD')
    root.style.setProperty('--color-primary-light', '#2F2A63')
    root.style.setProperty('--color-primary-disabled-gradient-start', '#383170')
    root.style.setProperty('--color-primary-disabled-bg', '#2F2A63')
    root.style.setProperty('--color-primary-disabled-border', '#2F2A63')
    root.style.setProperty('--color-primary-disabled-text', 'rgba(255, 255, 255, 0.5)')
    root.style.setProperty('--tnav-bg', '#0F0F17')
    root.style.setProperty('--tnav-overlay', 'linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0))')
    root.style.setProperty('--tnav-border', 'rgba(26, 30, 48, 0.75)')
    root.style.setProperty('--tnav-shadow', '0 12px 26px rgba(0, 0, 0, 0.22)')
    root.style.setProperty('--tnav-text', '#fff')
    root.style.setProperty('--nav-item-color', '#bec2d4')
    root.style.setProperty('--nav-item-hover-color', '#ffffff')
    root.style.setProperty('--nav-item-hover-bg', 'rgba(255, 255, 255, 0.055)')
    root.style.setProperty('--nav-item-active-color', '#fff')
    root.style.setProperty('--nav-item-active-bg', 'rgba(109, 94, 246, 0.14)')
    root.style.setProperty('--nav-active-accent', '#F5A623')
    root.style.setProperty('--nav-active-line-shadow', '0 -2px 14px rgba(245, 166, 35, 0.35)')
    root.style.setProperty('--brand-color', '#6D5EF6')
    root.style.setProperty('--brand-glow', 'rgba(109, 94, 246, 0.56)')
    root.style.setProperty('--logo-filter', 'drop-shadow(0 0 10px rgba(109, 94, 246, 0.5))')
    root.style.setProperty('--logo-core-bg', '#0F0F17')
    root.style.setProperty('--logo-strong-color', '#ffffff')
    root.style.setProperty('--logo-beam-a', 'linear-gradient(90deg, #A99BFF, #6D5EF6)')
    root.style.setProperty('--logo-beam-b', 'linear-gradient(90deg, var(--nav-active-accent), #ffffff)')
    root.style.setProperty('--icon-btn-color', '#aeb2c5')
    root.style.setProperty('--icon-btn-hover-bg', 'rgba(255, 255, 255, 0.08)')
    root.style.setProperty('--icon-btn-hover-color', '#ffffff')
    root.style.setProperty('--tz-color', '#aeb2c5')
    root.style.setProperty('--tz-bg', 'rgba(255, 255, 255, 0.045)')
    root.style.setProperty('--tz-border', 'rgba(255, 255, 255, 0.08)')
    root.style.setProperty('--nav-user-trigger-color', 'rgba(255, 255, 255, 0.88)')
    root.style.setProperty('--nav-user-trigger-hover-color', '#fff')
    root.style.setProperty('--nav-user-trigger-hover-bg', 'rgba(255, 255, 255, 0.14)')
    root.style.setProperty('--nav-user-trigger-caret-color', 'rgba(255, 255, 255, 0.72)')
    root.style.setProperty('--nav-user-trigger-caret-hover-color', 'rgba(255, 255, 255, 0.95)')
    root.style.setProperty('--nav-notification-color', 'rgba(255, 255, 255, 0.8)')
    root.style.setProperty('--nav-notification-hover-color', '#fff')
    root.style.setProperty('--sidebar-bg', '#0F0F17')
    root.style.setProperty('--sidebar-overlay', 'linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0))')
    root.style.setProperty('--sidebar-border', 'rgba(109, 94, 246, 0.22)')
    root.style.setProperty('--sidebar-text', '#C6C6D2')
    root.style.setProperty('--sidebar-hover-bg', 'rgba(109, 94, 246, 0.12)')
    root.style.setProperty('--sidebar-hover-color', '#FFFFFF')
    root.style.setProperty('--sidebar-active-bg', 'rgba(109, 94, 246, 0.24)')
    root.style.setProperty('--sidebar-active-color', '#FFFFFF')
    root.style.setProperty('--sidebar-active-line-bg', '#F5A623')
    root.style.setProperty('--sidebar-section', '#76768A')
    root.style.setProperty('--sidebar-subtext-color', '#C6C6D2')
    root.style.setProperty('--sidebar-shadow', '8px 0 24px rgba(9, 8, 15, 0.18)')
    root.style.setProperty('--content-bg', '#111116')
    root.style.setProperty('--page-bg', '#1A1A22')
    root.style.setProperty('--page-border', '#2A2B35')
    root.style.setProperty('--page-text', '#E2E2E2')
    root.style.setProperty('--page-text-secondary', '#9A9CAE')
    // Card, border and text semantic colors for dark mode
    root.style.setProperty('--color-card-bg', '#1C1D24')
    root.style.setProperty('--color-border', '#2A2B35')
    root.style.setProperty('--color-border-light', '#3A3B45')
    root.style.setProperty('--color-text-title', '#E2E2E2')
    root.style.setProperty('--color-text-primary', '#A0A0A8')
    root.style.setProperty('--color-text-secondary', '#787880')
    root.style.setProperty('--color-text-tertiary', '#5A5A65')
    root.style.setProperty('--color-text-placeholder', '#5A5A65')
    root.style.setProperty('--color-text-disabled', '#404048')
    root.style.setProperty('--color-grey-1', '#1C1D24')
    root.style.setProperty('--color-grey-2', '#252630')
    // Element Plus component dark mode overrides
    root.style.setProperty('--el-fill-color-blank', '#1C1D24')
    root.style.setProperty('--el-bg-color', '#141414')
    root.style.setProperty('--el-bg-color-overlay', '#1C1D24')
    root.style.setProperty('--el-popper-bg', '#1C1D24')
    root.style.setProperty('--el-popper-shadow-opacity', '0.35')
    root.style.setProperty('--hfl-popper-radius', '10px')
    root.style.setProperty('--el-popper-border-radius', '10px')
    root.style.setProperty('--el-text-color-primary', '#E2E2E2')
    root.style.setProperty('--el-text-color-regular', '#A0A0A8')
    root.style.setProperty('--el-text-color-secondary', '#787880')
    root.style.setProperty('--el-text-color-placeholder', '#5A5A65')
    root.style.setProperty('--el-text-color-disabled', '#404048')
    root.style.setProperty('--el-border-color', '#2A2B35')
    root.style.setProperty('--el-border-color-light', '#3A3B45')
    root.style.setProperty('--el-border-color-lighter', '#3A3B45')
    root.style.setProperty('--el-fill-color', '#252630')
    root.style.setProperty('--el-fill-color-light', '#1C1D24')
    root.style.setProperty('--el-fill-color-lighter', '#1C1D24')
    root.style.setProperty('--el-mask-color', '#00000080')
    root.style.setProperty('--el-overlay-color', '#000000b3')
    root.style.setProperty('--el-overlay-color-light', '#00000066')
    // Button hover/focus: use subtle primary tint instead of white
    root.style.setProperty('--el-button-hover-bg-color', 'rgba(109, 94, 246, 0.15)')
    root.style.setProperty('--el-button-hover-border-color', 'rgba(109, 94, 246, 0.4)')
    root.style.setProperty('--el-button-hover-text-color', '#A99BFF')
    root.style.setProperty('--el-color-primary-light-9', 'rgba(109, 94, 246, 0.12)')
    root.style.setProperty('--el-color-primary-light-7', 'rgba(109, 94, 246, 0.24)')
  } else if (t === 'light') {
    root.style.setProperty('--color-primary', '#6D5EF6')
    root.style.setProperty('--color-primary-gradient-start', '#7E6CEF')
    root.style.setProperty('--color-primary-gradient-end', '#6D5EF6')
    root.style.setProperty('--color-primary-hover-gradient-start', '#8876F5')
    root.style.setProperty('--color-primary-hover-gradient-end', '#7664FA')
    root.style.setProperty('--color-primary-active-gradient-start', '#5F50E0')
    root.style.setProperty('--color-primary-active-gradient-end', '#5546D8')
    root.style.setProperty('--color-primary-hover', '#5546D8')
    root.style.setProperty('--color-primary-active', '#4536BD')
    root.style.setProperty('--color-primary-light', '#F2F0FE')
    root.style.setProperty('--color-primary-disabled-gradient-start', '#E3DFFF')
    root.style.setProperty('--color-primary-disabled-bg', '#D2CBFA')
    root.style.setProperty('--color-primary-disabled-border', '#D2CBFA')
    root.style.setProperty('--color-primary-disabled-text', '#6F63B7')
    root.style.setProperty('--tnav-bg', '#0F0F17')
    root.style.setProperty('--tnav-overlay', 'linear-gradient(180deg, rgba(255, 255, 255, 0.045), rgba(255, 255, 255, 0))')
    root.style.setProperty('--tnav-border', 'rgba(26, 30, 48, 0.75)')
    root.style.setProperty('--tnav-shadow', '0 12px 26px rgba(23, 27, 45, 0.18)')
    root.style.setProperty('--tnav-text', '#fff')
    root.style.setProperty('--nav-item-color', '#bec2d4')
    root.style.setProperty('--nav-item-hover-color', '#ffffff')
    root.style.setProperty('--nav-item-hover-bg', 'rgba(255, 255, 255, 0.055)')
    root.style.setProperty('--nav-item-active-color', '#fff')
    root.style.setProperty('--nav-item-active-bg', 'rgba(109, 94, 246, 0.14)')
    root.style.setProperty('--nav-active-accent', '#F5A623')
    root.style.setProperty('--nav-active-line-shadow', '0 -2px 14px rgba(245, 166, 35, 0.35)')
    root.style.setProperty('--brand-color', '#6D5EF6')
    root.style.setProperty('--brand-glow', 'rgba(109, 94, 246, 0.5)')
    root.style.setProperty('--logo-filter', 'drop-shadow(0 0 10px rgba(109, 94, 246, 0.45))')
    root.style.setProperty('--logo-core-bg', '#0F0F17')
    root.style.setProperty('--logo-strong-color', '#ffffff')
    root.style.setProperty('--logo-beam-a', 'linear-gradient(90deg, #A99BFF, #6D5EF6)')
    root.style.setProperty('--logo-beam-b', 'linear-gradient(90deg, var(--nav-active-accent), #ffffff)')
    root.style.setProperty('--icon-btn-color', '#aeb2c5')
    root.style.setProperty('--icon-btn-hover-bg', 'rgba(255, 255, 255, 0.08)')
    root.style.setProperty('--icon-btn-hover-color', '#ffffff')
    root.style.setProperty('--tz-color', '#aeb2c5')
    root.style.setProperty('--tz-bg', 'rgba(255, 255, 255, 0.045)')
    root.style.setProperty('--tz-border', 'rgba(255, 255, 255, 0.08)')
    root.style.setProperty('--nav-user-trigger-color', 'rgba(255, 255, 255, 0.88)')
    root.style.setProperty('--nav-notification-color', 'rgba(255, 255, 255, 0.8)')
    root.style.setProperty('--nav-notification-hover-color', '#fff')
    root.style.setProperty('--nav-user-trigger-color', 'rgba(255, 255, 255, 0.88)')
    root.style.setProperty('--nav-user-trigger-hover-color', '#fff')
    root.style.setProperty('--nav-user-trigger-hover-bg', 'rgba(255, 255, 255, 0.14)')
    root.style.setProperty('--nav-user-trigger-caret-color', 'rgba(255, 255, 255, 0.72)')
    root.style.setProperty('--nav-user-trigger-caret-hover-color', 'rgba(255, 255, 255, 0.95)')
    root.style.setProperty('--sidebar-bg', '#0F0F17')
    root.style.setProperty('--sidebar-overlay', 'linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0))')
    root.style.setProperty('--sidebar-border', 'rgba(109, 94, 246, 0.22)')
    root.style.setProperty('--sidebar-text', '#C6C6D2')
    root.style.setProperty('--sidebar-hover-bg', 'rgba(109, 94, 246, 0.12)')
    root.style.setProperty('--sidebar-hover-color', '#FFFFFF')
    root.style.setProperty('--sidebar-active-bg', 'rgba(109, 94, 246, 0.24)')
    root.style.setProperty('--sidebar-active-color', '#FFFFFF')
    root.style.setProperty('--sidebar-active-line-bg', '#F5A623')
    root.style.setProperty('--sidebar-section', '#76768A')
    root.style.setProperty('--sidebar-subtext-color', '#C6C6D2')
    root.style.setProperty('--sidebar-shadow', '8px 0 24px rgba(9, 8, 15, 0.18)')
    root.style.setProperty('--content-bg', '#F4F4F7')
    root.style.setProperty('--page-bg', '#ffffff')
    root.style.setProperty('--page-border', '#E9E9EF')
    root.style.setProperty('--page-text', '#1C1C26')
    root.style.setProperty('--page-text-secondary', '#70707E')
    // Semantic card, border and text colors for light mode
    root.style.setProperty('--color-card-bg', '#ffffff')
    root.style.setProperty('--color-border', '#E9E9EF')
    root.style.setProperty('--color-border-light', '#F2F2F6')
    root.style.setProperty('--color-text-title', '#1C1C26')
    root.style.setProperty('--color-text-primary', '#3A3A48')
    root.style.setProperty('--color-text-secondary', '#70707E')
    root.style.setProperty('--color-text-tertiary', '#9C9CAA')
    root.style.setProperty('--color-text-placeholder', '#9C9CAA')
    root.style.setProperty('--color-text-disabled', '#BFBFC8')
    root.style.setProperty('--color-grey-1', '#FAFAFA')
    root.style.setProperty('--color-grey-2', '#F5F5F7')
    root.style.setProperty('--color-grey-3', '#F0F0F4')
    // Element Plus light mode overrides
    root.style.setProperty('--el-fill-color-blank', '#ffffff')
    root.style.setProperty('--el-bg-color', '#ffffff')
    root.style.setProperty('--el-bg-color-page', '#F4F4F7')
    root.style.setProperty('--el-bg-color-overlay', '#ffffff')
    root.style.setProperty('--el-text-color-primary', '#1C1C26')
    root.style.setProperty('--el-text-color-regular', '#3A3A48')
    root.style.setProperty('--el-text-color-secondary', '#70707E')
    root.style.setProperty('--el-text-color-placeholder', '#9C9CAA')
    root.style.setProperty('--el-text-color-disabled', '#BFBFC8')
    root.style.setProperty('--el-border-color', '#E9E9EF')
    root.style.setProperty('--el-border-color-light', '#F2F2F6')
    root.style.setProperty('--el-border-color-lighter', '#F2F2F6')
    root.style.setProperty('--el-fill-color', '#F5F5F7')
    root.style.setProperty('--el-fill-color-light', '#F5F5F7')
    root.style.setProperty('--el-fill-color-lighter', '#FAFAFA')
    root.style.setProperty('--el-popper-bg', '#ffffff')
    root.style.setProperty('--el-popper-shadow-opacity', '0.08')
    root.style.setProperty('--hfl-popper-radius', '10px')
    root.style.setProperty('--el-popper-border-radius', '10px')
    root.style.setProperty('--el-mask-color', 'rgba(255, 255, 255, 0.8)')
    root.style.setProperty('--el-overlay-color', 'rgba(0, 0, 0, 0.5)')
    root.style.setProperty('--el-overlay-color-light', 'rgba(0, 0, 0, 0.2)')
  } else {
    // hybrid - reference visual style: dark navigation, light content
    root.style.setProperty('--color-primary', '#6D5EF6')
    root.style.setProperty('--color-primary-gradient-start', '#7E6CEF')
    root.style.setProperty('--color-primary-gradient-end', '#6D5EF6')
    root.style.setProperty('--color-primary-hover-gradient-start', '#8876F5')
    root.style.setProperty('--color-primary-hover-gradient-end', '#7664FA')
    root.style.setProperty('--color-primary-active-gradient-start', '#5F50E0')
    root.style.setProperty('--color-primary-active-gradient-end', '#5546D8')
    root.style.setProperty('--color-primary-hover', '#5546D8')
    root.style.setProperty('--color-primary-active', '#4536BD')
    root.style.setProperty('--color-primary-light', '#F2F0FE')
    root.style.setProperty('--color-primary-disabled-gradient-start', '#E3DFFF')
    root.style.setProperty('--color-primary-disabled-bg', '#D2CBFA')
    root.style.setProperty('--color-primary-disabled-border', '#D2CBFA')
    root.style.setProperty('--color-primary-disabled-text', '#6F63B7')
    root.style.setProperty('--nav-active-accent', '#F5A623')
    root.style.setProperty('--tnav-bg', '#0F0F17')
    root.style.setProperty('--tnav-overlay', 'linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0))')
    root.style.setProperty('--tnav-border', 'rgba(109, 94, 246, 0.24)')
    root.style.setProperty('--tnav-shadow', '0 1px 0 rgba(255, 255, 255, 0.04), 0 12px 28px rgba(9, 8, 15, 0.28)')
    root.style.setProperty('--tnav-text', '#f8fafc')
    root.style.setProperty('--nav-item-color', '#9ca3af')
    root.style.setProperty('--nav-item-hover-color', '#e5e7eb')
    root.style.setProperty('--nav-item-hover-bg', 'rgba(109, 94, 246, 0.08)')
    root.style.setProperty('--nav-item-active-color', '#ffffff')
    root.style.setProperty('--nav-item-active-bg', 'rgba(109, 94, 246, 0.1)')
    root.style.setProperty('--nav-item-active-overlay', 'none')
    root.style.setProperty('--nav-active-line-start', '#F5A623')
    root.style.setProperty('--nav-active-line-end', '#F5A623')
    root.style.setProperty('--nav-active-line-shadow', '0 -2px 18px rgba(245, 166, 35, 0.5)')
    root.style.setProperty('--brand-color', '#6D5EF6')
    root.style.setProperty('--brand-glow', 'rgba(109, 94, 246, 0.64)')
    root.style.setProperty('--logo-filter', 'drop-shadow(0 0 10px rgba(109, 94, 246, 0.5))')
    root.style.setProperty('--logo-core-bg', '#0F0F17')
    root.style.setProperty('--logo-strong-color', '#f8fafc')
    root.style.setProperty('--logo-beam-a', 'linear-gradient(90deg, #A99BFF, #6D5EF6)')
    root.style.setProperty('--logo-beam-b', 'linear-gradient(90deg, var(--nav-active-accent), #f8fafc)')
    root.style.setProperty('--icon-btn-color', '#a1a1aa')
    root.style.setProperty('--icon-btn-hover-bg', 'rgba(109, 94, 246, 0.12)')
    root.style.setProperty('--icon-btn-hover-color', '#ffffff')
    root.style.setProperty('--tz-color', '#cbd5e1')
    root.style.setProperty('--tz-bg', 'rgba(22, 163, 74, 0.12)')
    root.style.setProperty('--tz-border', 'rgba(34, 197, 94, 0.24)')
    root.style.setProperty('--nav-user-trigger-color', '#f8fafc')
    root.style.setProperty('--nav-user-trigger-hover-color', '#ffffff')
    root.style.setProperty('--nav-user-trigger-hover-bg', 'rgba(109, 94, 246, 0.12)')
    root.style.setProperty('--nav-user-trigger-caret-color', '#a1a1aa')
    root.style.setProperty('--nav-user-trigger-caret-hover-color', '#f8fafc')
    root.style.setProperty('--nav-notification-color', '#a1a1aa')
    root.style.setProperty('--nav-notification-hover-color', '#ffffff')
    root.style.setProperty('--sidebar-bg', '#0F0F17')
    root.style.setProperty('--sidebar-overlay', 'linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0))')
    root.style.setProperty('--sidebar-border', 'rgba(109, 94, 246, 0.22)')
    root.style.setProperty('--sidebar-text', '#C6C6D2')
    root.style.setProperty('--sidebar-hover-bg', 'rgba(109, 94, 246, 0.12)')
    root.style.setProperty('--sidebar-hover-color', '#FFFFFF')
    root.style.setProperty('--sidebar-active-bg', 'rgba(109, 94, 246, 0.24)')
    root.style.setProperty('--sidebar-active-color', '#FFFFFF')
    root.style.setProperty('--sidebar-active-line-bg', '#F5A623')
    root.style.setProperty('--sidebar-section', '#76768A')
    root.style.setProperty('--sidebar-subtext-color', '#A8A8B8')
    root.style.setProperty('--sidebar-shadow', '8px 0 24px rgba(9, 8, 15, 0.18)')
    root.style.setProperty('--content-bg', '#F4F4F7')
    root.style.setProperty('--page-bg', '#ffffff')
    root.style.setProperty('--page-border', '#E9E9EF')
    root.style.setProperty('--page-text', '#1C1C26')
    root.style.setProperty('--page-text-secondary', '#70707E')
    // Semantic card, border and text colors for hybrid (light content)
    root.style.setProperty('--color-card-bg', '#ffffff')
    root.style.setProperty('--color-border', '#E9E9EF')
    root.style.setProperty('--color-border-light', '#F2F2F6')
    root.style.setProperty('--color-text-title', '#1C1C26')
    root.style.setProperty('--color-text-primary', '#3A3A48')
    root.style.setProperty('--color-text-secondary', '#70707E')
    root.style.setProperty('--color-text-tertiary', '#9C9CAA')
    root.style.setProperty('--color-text-placeholder', '#9C9CAA')
    root.style.setProperty('--color-text-disabled', '#BFBFC8')
    root.style.setProperty('--color-grey-1', '#FAFAFA')
    root.style.setProperty('--color-grey-2', '#F5F5F7')
    root.style.setProperty('--color-grey-3', '#F0F0F4')
    // Element Plus light mode overrides for content area
    root.style.setProperty('--el-fill-color-blank', '#ffffff')
    root.style.setProperty('--el-bg-color', '#ffffff')
    root.style.setProperty('--el-bg-color-page', '#F4F4F7')
    root.style.setProperty('--el-bg-color-overlay', '#ffffff')
    root.style.setProperty('--el-text-color-primary', '#1C1C26')
    root.style.setProperty('--el-text-color-regular', '#3A3A48')
    root.style.setProperty('--el-text-color-secondary', '#70707E')
    root.style.setProperty('--el-text-color-placeholder', '#9C9CAA')
    root.style.setProperty('--el-text-color-disabled', '#BFBFC8')
    root.style.setProperty('--el-border-color', '#E9E9EF')
    root.style.setProperty('--el-border-color-light', '#F2F2F6')
    root.style.setProperty('--el-border-color-lighter', '#F2F2F6')
    root.style.setProperty('--el-fill-color', '#F5F5F7')
    root.style.setProperty('--el-fill-color-light', '#FAFAFA')
    root.style.setProperty('--el-fill-color-lighter', '#FBFBFD')
    root.style.setProperty('--el-popper-bg', '#ffffff')
    root.style.setProperty('--el-popper-shadow-opacity', '0.08')
    root.style.setProperty('--hfl-popper-radius', '10px')
    root.style.setProperty('--el-popper-border-radius', '10px')
    root.style.setProperty('--el-mask-color', 'rgba(255, 255, 255, 0.8)')
    root.style.setProperty('--el-overlay-color', 'rgba(0, 0, 0, 0.5)')
    root.style.setProperty('--el-overlay-color-light', 'rgba(0, 0, 0, 0.2)')
  }
}

</script>

<template>
  <div
    class="min-h-screen"
    :data-theme-version="themeVersion"
    :style="{ backgroundColor: 'var(--content-bg)', '--topnav-height': 'var(--app-header-height)' }"
  >
    <TopNav
      :mobile-menu-open="mobileNavigationOpen"
      :admin-console-href="adminConsoleHref"
      :timezone-display="timezoneDisplay"
      :timezone-offset-display="timezoneOffsetDisplay"
      @toggle-mobile-menu="mobileNavigationOpen = !mobileNavigationOpen"
    />
    <MobileNavigationDrawer
      v-model="mobileNavigationOpen"
      :title="t('nav.navigation')"
      :primary-items="primaryNavItems"
      :module-items="fallbackMenuItems"
      :admin-console-href="adminConsoleHref"
      :show-organization-switcher="showOrganizationSwitcher"
      :timezone-offset-display="timezoneOffsetDisplay"
    />
    <div
      v-if="supportOrgKey"
      class="support-banner"
    >
      {{ t('common.supportModeBanner', { org: supportOrgKey }) }}
    </div>
    <main class="content-wrapper">
      <RouterView v-slot="{ Component, route: matchedRoute }">
        <Suspense
          :key="
            matchedRoute.path === '/protection/backups' || matchedRoute.path === '/ops/tasks'
              ? matchedRoute.path
              : matchedRoute.fullPath
          "
          timeout="0"
        >
          <component :is="Component" />
          <template #fallback>
            <div
              class="app-main-route-fallback"
              :class="{
                'app-main-route-fallback--collapsed': fallbackHasSidebar && fallbackSidebarCollapsed,
                'app-main-route-fallback--fullscreen': fallbackIsFullscreen,
                'app-main-route-fallback--no-sidebar': !fallbackHasSidebar,
              }"
              role="status"
              aria-label="Loading"
            >
              <div class="app-main-route-fallback__side">
                <Sidebar
                  v-if="fallbackHasSidebar"
                  :collapsed="fallbackSidebarCollapsed"
                  :menus="fallbackMenuItems"
                  @toggle="toggleFallbackSidebar"
                />
              </div>
              <section class="app-main-route-fallback__content">
                <div
                  v-if="fallbackIsFullscreen"
                  class="app-route-skeleton app-route-generic-skeleton"
                  aria-hidden="true"
                >
                  <div class="app-route-generic-skeleton__header">
                    <span class="app-route-generic-skeleton__back" />
                    <div class="app-route-generic-skeleton__title-group">
                      <span />
                      <i />
                    </div>
                  </div>
                  <div class="app-route-generic-skeleton__body">
                    <section class="app-route-generic-skeleton__section app-route-generic-skeleton__section--hero">
                      <span />
                      <i />
                      <b />
                    </section>
                    <section
                      v-for="idx in 3"
                      :key="`generic-section-${idx}`"
                      class="app-route-generic-skeleton__section"
                    >
                      <span />
                      <i />
                      <i />
                      <b />
                    </section>
                  </div>
                  <div class="app-route-generic-skeleton__footer">
                    <span />
                    <i />
                  </div>
                </div>
                <div
                  v-else-if="fallbackIsBackupFlow"
                  class="app-route-skeleton app-route-skeleton--backup-flow"
                  aria-hidden="true"
                >
                  <div class="app-route-skeleton__header">
                    <span class="app-route-skeleton__title" />
                    <div class="app-route-skeleton__actions">
                      <span />
                      <span />
                    </div>
                  </div>
                  <div class="app-route-flow-skeleton__steps">
                    <template
                      v-for="idx in 3"
                      :key="`flow-step-${idx}`"
                    >
                      <span class="app-route-flow-skeleton__step">
                        <i />
                        <b />
                        <em />
                        <small />
                      </span>
                      <span
                        v-if="idx < 3"
                        class="app-route-flow-skeleton__connector"
                      />
                    </template>
                  </div>
                  <div class="app-route-skeleton__panel app-route-flow-skeleton__panel">
                    <div class="app-route-skeleton__toolbar">
                      <span />
                      <i />
                    </div>
                    <div class="app-route-skeleton__table">
                      <span
                        v-for="idx in 8"
                        :key="`flow-row-${idx}`"
                      >
                        <i />
                        <i />
                        <i />
                        <i />
                      </span>
                    </div>
                  </div>
                </div>
                <div
                  v-else-if="fallbackIsDashboard"
                  class="app-route-skeleton app-route-dashboard-skeleton"
                  aria-hidden="true"
                >
                  <div class="app-route-dashboard-skeleton__pipeline">
                    <div class="app-route-dashboard-skeleton__pipeline-head">
                      <span />
                      <i />
                    </div>
                    <div class="app-route-dashboard-skeleton__pipeline-flow">
                      <template
                        v-for="idx in 3"
                        :key="`dashboard-pipeline-${idx}`"
                      >
                        <span class="app-route-dashboard-skeleton__pipeline-step">
                          <i />
                          <b />
                          <em />
                          <small />
                        </span>
                        <span
                          v-if="idx < 3"
                          class="app-route-dashboard-skeleton__pipeline-connector"
                        />
                      </template>
                    </div>
                  </div>
                  <div class="app-route-dashboard-skeleton__grid">
                    <span
                      v-for="idx in 3"
                      :key="`dashboard-card-${idx}`"
                      class="app-route-dashboard-skeleton__card"
                    >
                      <i />
                      <b />
                      <em />
                    </span>
                  </div>
                  <div class="app-route-dashboard-skeleton__cockpit">
                    <span class="app-route-dashboard-skeleton__panel app-route-dashboard-skeleton__panel--attention">
                      <i />
                      <b
                        v-for="idx in 5"
                        :key="`dashboard-attention-${idx}`"
                      />
                    </span>
                    <span class="app-route-dashboard-skeleton__panel app-route-dashboard-skeleton__panel--storage">
                      <i />
                      <b
                        v-for="idx in 4"
                        :key="`dashboard-storage-${idx}`"
                      />
                    </span>
                    <span class="app-route-dashboard-skeleton__panel app-route-dashboard-skeleton__panel--quota">
                      <i />
                      <b
                        v-for="idx in 4"
                        :key="`dashboard-quota-${idx}`"
                      />
                    </span>
                    <span class="app-route-dashboard-skeleton__panel app-route-dashboard-skeleton__panel--running">
                      <i />
                      <b
                        v-for="idx in 4"
                        :key="`dashboard-running-${idx}`"
                      />
                    </span>
                    <span class="app-route-dashboard-skeleton__panel app-route-dashboard-skeleton__panel--chart">
                      <i />
                      <em
                        v-for="idx in 7"
                        :key="`dashboard-chart-${idx}`"
                      />
                    </span>
                  </div>
                </div>
                <div
                  v-else-if="fallbackIsHostMonitor"
                  class="app-route-skeleton app-route-monitor-skeleton"
                  aria-hidden="true"
                >
                  <div class="app-route-skeleton__header">
                    <span class="app-route-skeleton__title" />
                    <div class="app-route-skeleton__actions">
                      <span />
                      <span />
                    </div>
                  </div>
                  <div class="app-route-skeleton__stats">
                    <span
                      v-for="idx in 4"
                      :key="`monitor-kpi-${idx}`"
                      class="app-route-skeleton__stat"
                    >
                      <i />
                      <b />
                      <em />
                    </span>
                  </div>
                  <div class="app-route-monitor-skeleton__charts">
                    <span
                      v-for="idx in 4"
                      :key="`monitor-chart-${idx}`"
                      class="app-route-monitor-skeleton__chart"
                    >
                      <i />
                      <b />
                    </span>
                  </div>
                  <div class="app-route-monitor-skeleton__charts app-route-monitor-skeleton__charts--pair">
                    <span
                      v-for="idx in 2"
                      :key="`monitor-wide-chart-${idx}`"
                      class="app-route-monitor-skeleton__chart"
                    >
                      <i />
                      <b />
                    </span>
                  </div>
                </div>
                <div
                  v-else-if="fallbackIsCopilot"
                  class="app-route-skeleton app-route-copilot-skeleton"
                  aria-hidden="true"
                >
                  <aside class="app-route-copilot-skeleton__aside">
                    <span />
                    <i
                      v-for="idx in 7"
                      :key="`copilot-session-${idx}`"
                    />
                  </aside>
                  <section class="app-route-copilot-skeleton__main">
                    <div class="app-route-copilot-skeleton__topbar">
                      <span />
                      <i />
                    </div>
                    <div class="app-route-copilot-skeleton__messages">
                      <span class="app-route-copilot-skeleton__bubble app-route-copilot-skeleton__bubble--ai" />
                      <span class="app-route-copilot-skeleton__bubble app-route-copilot-skeleton__bubble--user" />
                      <span class="app-route-copilot-skeleton__bubble app-route-copilot-skeleton__bubble--ai app-route-copilot-skeleton__bubble--long" />
                    </div>
                    <div class="app-route-copilot-skeleton__composer" />
                  </section>
                </div>
                <div
                  v-else-if="fallbackIsAccountProfile"
                  class="app-route-skeleton app-route-profile-skeleton"
                  aria-hidden="true"
                >
                  <div class="app-route-skeleton__header">
                    <span class="app-route-skeleton__title" />
                  </div>
                  <section
                    v-for="idx in 3"
                    :key="`profile-section-${idx}`"
                    class="app-route-profile-skeleton__section"
                  >
                    <span />
                    <i
                      v-for="row in idx === 2 ? 4 : 3"
                      :key="`profile-row-${idx}-${row}`"
                    />
                  </section>
                </div>
                <div
                  v-else-if="fallbackIsNodeSubscription"
                  class="app-route-skeleton app-route-subscription-skeleton"
                  aria-hidden="true"
                >
                  <section class="app-route-subscription-skeleton__section app-route-subscription-skeleton__section--overview">
                    <div class="app-route-subscription-skeleton__section-head">
                      <span />
                      <i />
                    </div>
                    <div class="app-route-subscription-skeleton__overview-grid">
                      <b
                        v-for="idx in 4"
                        :key="`subscription-overview-${idx}`"
                      />
                    </div>
                  </section>
                  <section class="app-route-subscription-skeleton__section">
                    <div class="app-route-subscription-skeleton__section-head">
                      <span />
                    </div>
                    <div class="app-route-subscription-skeleton__quota-grid">
                      <b
                        v-for="idx in 4"
                        :key="`subscription-quota-${idx}`"
                      />
                    </div>
                  </section>
                  <section class="app-route-subscription-skeleton__section app-route-subscription-skeleton__section--activation">
                    <div class="app-route-subscription-skeleton__section-head">
                      <span />
                    </div>
                    <div class="app-route-subscription-skeleton__activation">
                      <div class="app-route-subscription-skeleton__activation-step">
                        <span />
                        <div class="app-route-subscription-skeleton__code-row">
                          <i />
                          <b />
                        </div>
                        <em />
                      </div>
                      <div class="app-route-subscription-skeleton__activation-step">
                        <span />
                        <i />
                        <b />
                      </div>
                    </div>
                  </section>
                  <section class="app-route-subscription-skeleton__section app-route-subscription-skeleton__section--history">
                    <div class="app-route-subscription-skeleton__section-head">
                      <span />
                    </div>
                    <div class="app-route-skeleton__panel">
                      <div class="app-route-skeleton__table">
                        <span
                          v-for="idx in 5"
                          :key="`subscription-history-${idx}`"
                        >
                          <i />
                          <i />
                          <i />
                          <i />
                        </span>
                      </div>
                    </div>
                  </section>
                </div>
                <div
                  v-else-if="fallbackIsNodeSystem"
                  class="app-route-skeleton app-route-system-skeleton"
                  aria-hidden="true"
                >
                  <div class="app-route-system-skeleton__panel">
                    <section
                      v-for="idx in 2"
                      :key="`system-row-${idx}`"
                      class="app-route-system-skeleton__row"
                    >
                      <span />
                      <b />
                      <i />
                    </section>
                  </div>
                  <div class="app-route-system-skeleton__footer">
                    <span />
                  </div>
                </div>
                <div
                  v-else
                  class="app-route-skeleton"
                  aria-hidden="true"
                >
                  <div class="app-route-skeleton__header">
                    <span class="app-route-skeleton__title" />
                    <div class="app-route-skeleton__actions">
                      <span />
                      <span />
                    </div>
                  </div>
                  <div class="app-route-skeleton__panel">
                    <div class="app-route-skeleton__toolbar">
                      <span />
                      <i />
                    </div>
                    <div class="app-route-skeleton__table">
                      <span
                        v-for="idx in 8"
                        :key="`row-${idx}`"
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
.support-banner {
  background: rgba(245, 158, 11, 0.15);
  border-bottom: 1px solid rgba(245, 158, 11, 0.35);
  color: rgb(146 64 14);
  font-size: 13px;
  font-weight: 600;
  padding: 8px 16px;
  text-align: center;
}

.content-wrapper {
  position: relative;
  width: 100%;
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
}

.app-main-route-fallback {
  display: grid;
  grid-template-columns: 238px minmax(0, 1fr);
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
  background: var(--content-bg, #f4f4f7);
}

.app-main-route-fallback--collapsed {
  grid-template-columns: 56px minmax(0, 1fr);
}

.app-main-route-fallback--no-sidebar {
  grid-template-columns: minmax(0, 1fr);
}

.app-main-route-fallback--fullscreen {
  background: var(--content-bg, #f4f4f7);
}

.app-main-route-fallback__side {
  position: sticky;
  top: var(--topnav-height, 52px);
  display: flex;
  height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
  min-width: 0;
  flex-shrink: 0;
}

.app-main-route-fallback--no-sidebar .app-main-route-fallback__side {
  display: none;
}

.app-main-route-fallback__content {
  display: block;
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
  padding: 20px 24px 28px;
  overflow: hidden;
}

.app-main-route-fallback--fullscreen .app-main-route-fallback__content {
  padding: 24px clamp(16px, 3vw, 32px) 32px;
}

.app-route-skeleton {
  display: flex;
  flex-direction: column;
  width: 100%;
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)) - 48px);
  animation: app-route-skeleton-fade 180ms ease-out;
}

.app-route-skeleton__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 18px;
}

.app-route-skeleton__title,
.app-route-skeleton__actions span,
.app-route-skeleton__stat,
.app-route-flow-skeleton__step,
.app-route-flow-skeleton__connector,
.app-route-dashboard-skeleton__pipeline-step,
.app-route-dashboard-skeleton__pipeline-connector,
.app-route-dashboard-skeleton__card,
.app-route-dashboard-skeleton__panel,
.app-route-monitor-skeleton__chart,
.app-route-copilot-skeleton__aside,
.app-route-copilot-skeleton__main,
.app-route-generic-skeleton__back,
.app-route-generic-skeleton__title-group span,
.app-route-generic-skeleton__title-group i,
.app-route-generic-skeleton__section,
.app-route-generic-skeleton__footer span,
.app-route-generic-skeleton__footer i,
.app-route-profile-skeleton__section,
.app-route-subscription-skeleton__overview-grid b,
.app-route-subscription-skeleton__quota-grid b,
.app-route-system-skeleton__panel,
.app-route-system-skeleton__footer span,
.app-route-skeleton__toolbar span,
.app-route-skeleton__toolbar i,
.app-route-skeleton__table i {
  position: relative;
  overflow: hidden;
  background: var(--color-card-bg, #fff);
}

.app-route-skeleton__title::after,
.app-route-skeleton__actions span::after,
.app-route-skeleton__stat::after,
.app-route-flow-skeleton__step::after,
.app-route-flow-skeleton__connector::after,
.app-route-dashboard-skeleton__pipeline-step::after,
.app-route-dashboard-skeleton__pipeline-connector::after,
.app-route-dashboard-skeleton__card::after,
.app-route-dashboard-skeleton__panel::after,
.app-route-monitor-skeleton__chart::after,
.app-route-copilot-skeleton__aside::after,
.app-route-copilot-skeleton__main::after,
.app-route-generic-skeleton__back::after,
.app-route-generic-skeleton__title-group span::after,
.app-route-generic-skeleton__title-group i::after,
.app-route-generic-skeleton__section::after,
.app-route-generic-skeleton__footer span::after,
.app-route-generic-skeleton__footer i::after,
.app-route-profile-skeleton__section::after,
.app-route-system-skeleton__panel::after,
.app-route-system-skeleton__footer span::after,
.app-route-skeleton__toolbar span::after,
.app-route-skeleton__toolbar i::after,
.app-route-skeleton__table i::after {
  content: '';
  position: absolute;
  inset: 0;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(109, 94, 246, 0.10), transparent);
  animation: app-route-skeleton-shimmer 1.15s ease-in-out infinite;
}

.app-route-skeleton__title {
  display: block;
  width: 220px;
  height: 28px;
  border-radius: 6px;
  border: 1px solid var(--color-border-light, #f2f2f6);
}

.app-route-skeleton__actions {
  display: flex;
  gap: 10px;
  flex-shrink: 0;
}

.app-route-skeleton__actions span {
  display: block;
  width: 92px;
  height: 34px;
  border-radius: 8px;
  border: 1px solid var(--color-border-light, #f2f2f6);
}

.app-route-generic-skeleton {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  width: min(1180px, 100%);
  height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)) - 56px);
  margin: 0 auto;
  gap: 18px;
}

.app-route-generic-skeleton__header {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  gap: 14px;
  align-items: center;
}

.app-route-generic-skeleton__back {
  display: block;
  width: 38px;
  height: 38px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.app-route-generic-skeleton__title-group {
  display: grid;
  gap: 10px;
  min-width: 0;
}

.app-route-generic-skeleton__title-group span,
.app-route-generic-skeleton__title-group i,
.app-route-generic-skeleton__section span,
.app-route-generic-skeleton__section i,
.app-route-generic-skeleton__section b {
  display: block;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-generic-skeleton__title-group span {
  width: min(300px, 72%);
  height: 24px;
}

.app-route-generic-skeleton__title-group i {
  width: min(520px, 92%);
  height: 12px;
}

.app-route-generic-skeleton__body {
  display: grid;
  grid-template-rows: 1.15fr repeat(3, minmax(0, 1fr));
  gap: 14px;
  min-height: 0;
}

.app-route-generic-skeleton__section {
  display: grid;
  align-content: center;
  gap: 14px;
  min-height: 0;
  padding: 20px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  box-shadow: 0 12px 28px rgba(28, 28, 38, 0.04);
}

.app-route-generic-skeleton__section--hero {
  align-content: center;
}

.app-route-generic-skeleton__section span {
  width: min(240px, 64%);
  height: 18px;
}

.app-route-generic-skeleton__section i {
  width: 100%;
  height: 12px;
}

.app-route-generic-skeleton__section i:nth-of-type(2) {
  width: 76%;
}

.app-route-generic-skeleton__section b {
  width: 42%;
  height: 12px;
}

.app-route-generic-skeleton__footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding-top: 2px;
}

.app-route-generic-skeleton__footer span,
.app-route-generic-skeleton__footer i {
  display: block;
  height: 34px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.app-route-generic-skeleton__footer span {
  width: 86px;
}

.app-route-generic-skeleton__footer i {
  width: 118px;
}

.app-route-skeleton__stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-bottom: 14px;
}

.app-route-skeleton__stat {
  display: grid;
  gap: 12px;
  min-height: 112px;
  padding: 16px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  box-shadow: 0 10px 24px rgba(28, 28, 38, 0.03);
}

.app-route-skeleton__stat i,
.app-route-skeleton__stat b,
.app-route-skeleton__stat em {
  display: block;
  height: 12px;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-skeleton__stat i {
  width: 42%;
}

.app-route-skeleton__stat b {
  width: 68%;
}

.app-route-skeleton__stat em {
  width: 100%;
}

.app-route-flow-skeleton__steps {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 36px minmax(0, 1fr) 36px minmax(0, 1fr);
  gap: 12px;
  align-items: stretch;
  margin-bottom: 14px;
}

.app-route-flow-skeleton__step {
  display: grid;
  grid-template-columns: 42px minmax(0, 1fr);
  grid-template-rows: auto auto auto;
  column-gap: 14px;
  row-gap: 12px;
  min-height: 132px;
  padding: 18px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  box-shadow: 0 10px 24px rgba(28, 28, 38, 0.03);
}

.app-route-flow-skeleton__step i,
.app-route-flow-skeleton__step b,
.app-route-flow-skeleton__step em,
.app-route-flow-skeleton__step small {
  display: block;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-flow-skeleton__step i {
  grid-row: 1 / 4;
  width: 42px;
  height: 42px;
  border-radius: 50%;
}

.app-route-flow-skeleton__step b {
  width: 58%;
  height: 14px;
  align-self: end;
}

.app-route-flow-skeleton__step em {
  width: 100%;
  height: 12px;
}

.app-route-flow-skeleton__step small {
  width: 42%;
  height: 12px;
}

.app-route-flow-skeleton__connector {
  align-self: center;
  height: 8px;
  border-radius: 999px;
  background: var(--color-card-bg, #fff);
}

.app-route-dashboard-skeleton {
  gap: 1.5rem;
}

.app-route-dashboard-skeleton__pipeline,
.app-route-dashboard-skeleton__pipeline-flow,
.app-route-dashboard-skeleton__grid,
.app-route-dashboard-skeleton__cockpit,
.app-route-monitor-skeleton__charts {
  display: grid;
  gap: 1.5rem;
}

.app-route-dashboard-skeleton__pipeline {
  gap: 0;
  padding: 1.5rem 2rem;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 1rem;
  background: var(--color-card-bg, #fff);
}

.app-route-dashboard-skeleton__pipeline-head {
  display: grid;
  gap: 10px;
  margin-bottom: 1.25rem;
  padding-bottom: 1.25rem;
  width: min(360px, 100%);
  border-bottom: 1px solid var(--color-border-light, #f2f2f6);
}

.app-route-dashboard-skeleton__pipeline-head span,
.app-route-dashboard-skeleton__pipeline-head i {
  display: block;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-dashboard-skeleton__pipeline-head span {
  width: 70%;
  height: 18px;
}

.app-route-dashboard-skeleton__pipeline-head i {
  width: 100%;
  height: 12px;
}

.app-route-dashboard-skeleton__pipeline-flow {
  display: flex;
  align-items: center;
  gap: 0;
  padding: 0.5rem 0;
}

.app-route-dashboard-skeleton__pipeline-step,
.app-route-dashboard-skeleton__card,
.app-route-dashboard-skeleton__panel {
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.app-route-dashboard-skeleton__pipeline-step {
  display: flex;
  flex: 1 1 0;
  flex-direction: column;
  align-items: center;
  width: 100%;
  max-width: 280px;
  min-height: 164px;
  padding: 0;
  border: 0;
}

.app-route-dashboard-skeleton__pipeline-step i,
.app-route-dashboard-skeleton__pipeline-step b,
.app-route-dashboard-skeleton__pipeline-step em,
.app-route-dashboard-skeleton__pipeline-step small,
.app-route-dashboard-skeleton__card i,
.app-route-dashboard-skeleton__card b,
.app-route-dashboard-skeleton__card em {
  display: block;
  height: 12px;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-dashboard-skeleton__pipeline-step i {
  width: 6rem;
  height: 6rem;
  margin-bottom: 1rem;
  border-radius: 50%;
}

.app-route-dashboard-skeleton__pipeline-step b,
.app-route-dashboard-skeleton__card b {
  width: 62%;
}

.app-route-dashboard-skeleton__pipeline-step em,
.app-route-dashboard-skeleton__card em {
  width: 84%;
}

.app-route-dashboard-skeleton__pipeline-step small {
  width: 46%;
  margin-top: 2px;
}

.app-route-dashboard-skeleton__pipeline-connector {
  flex: 1 1 120px;
  min-width: 120px;
  max-width: none;
  height: 2px;
  margin: 0 0.75rem;
  border-radius: 999px;
}

.app-route-dashboard-skeleton__grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.app-route-dashboard-skeleton__card {
  display: grid;
  align-content: space-between;
  gap: 1rem;
  min-height: 220px;
  padding: 1.25rem;
  border-left-width: 4px;
}

.app-route-dashboard-skeleton__card i {
  width: 38%;
}

.app-route-dashboard-skeleton__cockpit {
  flex: 1;
  min-height: 0;
  gap: 1.5rem;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  grid-template-rows: 352px 250px;
}

.app-route-dashboard-skeleton__panel {
  display: grid;
  align-content: start;
  gap: 1rem;
  padding: 1.25rem;
  border-radius: 0.75rem;
}

.app-route-dashboard-skeleton__panel--attention {
  grid-column: 1;
  grid-row: 1 / span 2;
}

.app-route-dashboard-skeleton__panel--storage {
  grid-column: 2;
  grid-row: 1;
}

.app-route-dashboard-skeleton__panel--quota {
  grid-column: 2;
  grid-row: 2;
}

.app-route-dashboard-skeleton__panel--running {
  grid-column: 3;
  grid-row: 1;
}

.app-route-dashboard-skeleton__panel--chart {
  grid-column: 3;
  grid-row: 2;
}

.app-route-dashboard-skeleton__panel i,
.app-route-dashboard-skeleton__panel b,
.app-route-dashboard-skeleton__panel em {
  display: block;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-dashboard-skeleton__panel i {
  width: 54%;
  height: 16px;
}

.app-route-dashboard-skeleton__panel b {
  height: 14px;
}

.app-route-dashboard-skeleton__panel--chart {
  grid-template-columns: repeat(7, minmax(0, 1fr));
  align-items: end;
}

.app-route-dashboard-skeleton__panel--chart i {
  grid-column: 1 / -1;
  align-self: start;
}

.app-route-dashboard-skeleton__panel--chart em {
  height: 48%;
}

.app-route-dashboard-skeleton__panel--chart em:nth-child(3),
.app-route-dashboard-skeleton__panel--chart em:nth-child(6) {
  height: 72%;
}

.app-route-dashboard-skeleton__panel--chart em:nth-child(4),
.app-route-dashboard-skeleton__panel--chart em:nth-child(8) {
  height: 34%;
}

.app-route-monitor-skeleton {
  flex: 1;
  gap: 14px;
}

.app-route-monitor-skeleton__charts {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.app-route-monitor-skeleton__charts--pair {
  flex: 1;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  min-height: 0;
}

.app-route-monitor-skeleton__chart {
  display: grid;
  grid-template-rows: auto 1fr;
  gap: 18px;
  min-height: 220px;
  padding: 16px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.app-route-monitor-skeleton__charts--pair .app-route-monitor-skeleton__chart {
  min-height: 0;
}

.app-route-monitor-skeleton__chart i,
.app-route-monitor-skeleton__chart b {
  display: block;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-monitor-skeleton__chart i {
  width: 42%;
  height: 14px;
}

.app-route-monitor-skeleton__chart b {
  width: 100%;
  height: 100%;
  min-height: 142px;
  border-radius: 8px;
}

.app-route-copilot-skeleton {
  display: grid;
  grid-template-columns: 248px minmax(0, 1fr);
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)) - 48px);
}

.app-route-copilot-skeleton__aside,
.app-route-copilot-skeleton__main {
  border: 1px solid var(--color-border-light, #f2f2f6);
}

.app-route-copilot-skeleton__aside {
  display: grid;
  align-content: start;
  gap: 12px;
  padding: 12px;
  border-radius: 8px 0 0 8px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-copilot-skeleton__aside span,
.app-route-copilot-skeleton__aside i,
.app-route-copilot-skeleton__topbar span,
.app-route-copilot-skeleton__topbar i,
.app-route-copilot-skeleton__bubble,
.app-route-copilot-skeleton__composer,
.app-route-profile-skeleton__section span,
.app-route-profile-skeleton__section i {
  display: block;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-copilot-skeleton__aside span {
  height: 34px;
  border-radius: 8px;
}

.app-route-copilot-skeleton__aside i {
  height: 38px;
  background: var(--color-card-bg, #fff);
}

.app-route-copilot-skeleton__main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  border-left: 0;
  border-radius: 0 8px 8px 0;
  background: var(--color-card-bg, #fff);
}

.app-route-copilot-skeleton__topbar {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  padding: 14px 16px;
  border-bottom: 1px solid var(--color-border-light, #f2f2f6);
}

.app-route-copilot-skeleton__topbar span {
  width: 320px;
  height: 38px;
}

.app-route-copilot-skeleton__topbar i {
  width: 180px;
  height: 38px;
}

.app-route-copilot-skeleton__messages {
  display: flex;
  flex: 1;
  flex-direction: column;
  gap: 18px;
  padding: 24px;
}

.app-route-copilot-skeleton__bubble {
  width: min(620px, 72%);
  height: 76px;
  border-radius: 12px;
}

.app-route-copilot-skeleton__bubble--user {
  align-self: flex-end;
  width: min(420px, 54%);
}

.app-route-copilot-skeleton__bubble--long {
  height: 132px;
}

.app-route-copilot-skeleton__composer {
  height: 72px;
  margin: 0 24px 20px;
  border-radius: 10px;
}

.app-route-profile-skeleton {
  gap: 14px;
}

.app-route-profile-skeleton__section {
  display: grid;
  gap: 16px;
  padding: 18px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
}

.app-route-profile-skeleton__section span {
  width: 220px;
  height: 18px;
}

.app-route-profile-skeleton__section i {
  width: 100%;
  height: 36px;
  border-radius: 8px;
}

.app-route-subscription-skeleton {
  gap: 16px;
}

.app-route-subscription-skeleton__section {
  position: relative;
  display: grid;
  gap: 16px;
  overflow: hidden;
  padding: 18px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
  box-shadow: 0 12px 28px rgba(28, 28, 38, 0.04);
}

.app-route-subscription-skeleton__section::after {
  content: '';
  position: absolute;
  inset: 0;
  pointer-events: none;
  transform: translateX(-100%);
  background: linear-gradient(90deg, transparent, rgba(109, 94, 246, 0.10), transparent);
  animation: app-route-skeleton-shimmer 1.15s ease-in-out infinite;
}

.app-route-subscription-skeleton__section--history {
  flex: 1;
  min-height: 0;
}

.app-route-subscription-skeleton__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}

.app-route-subscription-skeleton__section-head span,
.app-route-subscription-skeleton__section-head i,
.app-route-subscription-skeleton__overview-grid b::before,
.app-route-subscription-skeleton__quota-grid b::before,
.app-route-subscription-skeleton__activation-step span,
.app-route-subscription-skeleton__activation-step i,
.app-route-subscription-skeleton__activation-step b,
.app-route-subscription-skeleton__activation-step em,
.app-route-system-skeleton__row span,
.app-route-system-skeleton__row b,
.app-route-system-skeleton__row i {
  display: block;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-subscription-skeleton__section-head span {
  width: 180px;
  height: 18px;
}

.app-route-subscription-skeleton__section-head i {
  width: 32px;
  height: 32px;
  border-radius: 8px;
}

.app-route-subscription-skeleton__overview-grid,
.app-route-subscription-skeleton__quota-grid {
  display: grid;
  gap: 16px 24px;
}

.app-route-subscription-skeleton__overview-grid {
  grid-template-columns: repeat(4, minmax(0, 1fr));
}

.app-route-subscription-skeleton__quota-grid {
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
}

.app-route-subscription-skeleton__overview-grid b,
.app-route-subscription-skeleton__quota-grid b {
  display: grid;
  gap: 10px;
  min-height: 68px;
}

.app-route-subscription-skeleton__overview-grid b {
  background: var(--color-card-bg, #fff);
}

.app-route-subscription-skeleton__overview-grid b::before,
.app-route-subscription-skeleton__quota-grid b::before {
  content: '';
  width: 56%;
  height: 12px;
}

.app-route-subscription-skeleton__overview-grid b::after,
.app-route-subscription-skeleton__quota-grid b::after {
  content: '';
  display: block;
  height: 16px;
  width: 76%;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
  animation: none;
  transform: none;
}

.app-route-subscription-skeleton__quota-grid b {
  padding: 12px;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 12px;
  background: var(--color-card-bg, #fff);
}

.app-route-subscription-skeleton__activation {
  display: grid;
  gap: 24px;
}

.app-route-subscription-skeleton__activation-step {
  display: grid;
  gap: 12px;
}

.app-route-subscription-skeleton__activation-step span {
  width: 220px;
  height: 14px;
}

.app-route-subscription-skeleton__code-row {
  display: flex;
  align-items: stretch;
  gap: 12px;
}

.app-route-subscription-skeleton__code-row i {
  flex: 1 1 320px;
  height: 36px;
  border: 1px solid var(--color-border-light, #e4e7ed);
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
}

.app-route-subscription-skeleton__code-row b,
.app-route-subscription-skeleton__activation-step > b {
  width: 112px;
  height: 36px;
  justify-self: end;
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
  border: 1px solid var(--color-border-light, #f2f2f6);
}

.app-route-subscription-skeleton__activation-step em {
  width: min(520px, 100%);
  height: 12px;
}

.app-route-subscription-skeleton__activation-step > i {
  width: 100%;
  height: 94px;
  border: 1px solid var(--color-border-light, #e4e7ed);
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
}

.app-route-system-skeleton {
  gap: 0;
}

.app-route-system-skeleton__panel {
  display: grid;
  border: 1px solid var(--color-border-light, #f2f2f6);
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
  box-shadow: 0 12px 28px rgba(28, 28, 38, 0.04);
}

.app-route-system-skeleton__row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 136px;
  gap: 10px 24px;
  align-items: center;
  min-height: 86px;
  padding: 18px 24px;
  border-bottom: 1px solid var(--color-border-light, #f2f2f6);
}

.app-route-system-skeleton__row:last-child {
  border-bottom: 0;
}

.app-route-system-skeleton__row span {
  width: min(280px, 56%);
  height: 16px;
}

.app-route-system-skeleton__row b {
  width: min(560px, 100%);
  height: 12px;
}

.app-route-system-skeleton__row i {
  grid-column: 2;
  grid-row: 1 / 3;
  width: 136px;
  height: 32px;
  border-radius: 8px;
}

.app-route-system-skeleton__footer {
  display: flex;
  justify-content: flex-end;
  padding: 16px 24px 0;
}

.app-route-system-skeleton__footer span {
  display: block;
  width: 72px;
  height: 32px;
  border-radius: 8px;
  background: var(--color-card-bg, #fff);
}

.app-route-skeleton__panel {
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

.app-route-skeleton__toolbar {
  display: flex;
  flex-shrink: 0;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--color-border-light, #f2f2f6);
}

.app-route-skeleton__toolbar span {
  width: 260px;
  height: 32px;
  border-radius: 8px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-skeleton__toolbar i {
  width: 132px;
  height: 32px;
  border-radius: 8px;
  background: var(--color-grey-2, #f5f5f7);
}

.app-route-skeleton__table {
  display: grid;
  flex: 1;
  grid-template-rows: repeat(8, minmax(34px, 1fr));
  gap: 12px;
  min-height: 0;
  padding-top: 16px;
}

.app-route-skeleton__table span {
  display: grid;
  grid-template-columns: 1.4fr 1fr 0.8fr 0.9fr;
  gap: 18px;
  align-items: center;
  min-height: 0;
}

.app-route-skeleton__table i {
  display: block;
  height: 12px;
  border-radius: 999px;
  background: var(--color-grey-2, #f5f5f7);
}

@keyframes app-route-skeleton-shimmer {
  100% {
    transform: translateX(100%);
  }
}

@keyframes app-route-skeleton-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}

.app-main-route-loading {
  position: absolute;
  inset: 0;
  z-index: 5;
  display: grid;
  min-height: calc(var(--app-viewport-height) - var(--topnav-height, var(--app-header-height)));
  place-items: center;
  background: var(--content-bg, #f4f4f7);
}

.app-main-route-loading__spinner {
  width: 32px;
  height: 32px;
  border: 3px solid rgba(109, 94, 246, 0.16);
  border-top-color: var(--color-primary, #6d5ef6);
  border-radius: 9999px;
  animation: app-main-route-loading-spin 0.8s linear infinite;
}

@keyframes app-main-route-loading-spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1023.98px) {
  .app-main-route-fallback,
  .app-main-route-fallback--collapsed {
    grid-template-columns: minmax(0, 1fr);
  }

  .app-main-route-fallback__side {
    display: none;
  }

  .app-main-route-fallback__content {
    padding: 12px;
  }
}

@media (min-width: 768px) and (max-width: 1023.98px) {
  .app-route-dashboard-skeleton__pipeline {
    padding: 1.25rem 1.5rem;
  }

  .app-route-dashboard-skeleton__pipeline-head {
    margin-bottom: 0.875rem;
    padding-bottom: 0.875rem;
  }

  .app-route-dashboard-skeleton__pipeline-flow {
    padding: 0.25rem 0;
  }

  .app-route-dashboard-skeleton__pipeline-step {
    min-height: 132px;
  }

  .app-route-dashboard-skeleton__pipeline-step i {
    width: 4rem;
    height: 4rem;
    margin-bottom: 0.625rem;
  }

  .app-route-dashboard-skeleton__pipeline-connector {
    flex-basis: 80px;
    min-width: 48px;
    margin: 0 0.5rem;
  }
}

@media (max-width: 960px) {
  .app-route-dashboard-skeleton__pipeline,
  .app-route-dashboard-skeleton__pipeline-flow,
  .app-route-dashboard-skeleton__grid,
  .app-route-dashboard-skeleton__cockpit,
  .app-route-monitor-skeleton__charts,
  .app-route-monitor-skeleton__charts--pair,
  .app-route-copilot-skeleton {
    grid-template-columns: 1fr;
  }

  .app-route-dashboard-skeleton__cockpit {
    grid-template-rows: none;
  }

  .app-route-dashboard-skeleton__panel,
  .app-route-monitor-skeleton__chart {
    min-height: 220px;
  }

  .app-route-dashboard-skeleton__panel--attention,
  .app-route-dashboard-skeleton__panel--storage,
  .app-route-dashboard-skeleton__panel--quota,
  .app-route-dashboard-skeleton__panel--running,
  .app-route-dashboard-skeleton__panel--chart {
    grid-column: auto;
    grid-row: auto;
  }

  .app-route-copilot-skeleton__aside {
    display: none;
  }

  .app-route-copilot-skeleton__main {
    border-left: 1px solid var(--color-border-light, #f2f2f6);
    border-radius: 8px;
  }

  .app-route-copilot-skeleton__topbar {
    flex-direction: column;
  }

  .app-route-copilot-skeleton__topbar span,
  .app-route-copilot-skeleton__topbar i {
    width: 100%;
  }

  .app-route-flow-skeleton__steps {
    grid-template-columns: 1fr;
  }

  .app-route-flow-skeleton__connector {
    display: none;
  }

  .app-route-skeleton__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .app-route-skeleton__table span {
    grid-template-columns: 1.4fr 1fr;
  }

  .app-route-skeleton__table i:nth-child(n + 3) {
    display: none;
  }
}

@media (max-width: 767.98px) {
  .app-route-dashboard-skeleton__pipeline {
    padding: 1rem;
    border-radius: 0.75rem;
  }

  .app-route-dashboard-skeleton__pipeline-head {
    margin-bottom: 0.75rem;
    padding-bottom: 0.75rem;
  }

  .app-route-dashboard-skeleton__pipeline-flow {
    display: grid;
    grid-template-columns: minmax(0, 1fr) 24px minmax(0, 1fr) 24px minmax(0, 1fr);
    align-items: start;
    gap: 4px;
    padding: 0;
  }

  .app-route-dashboard-skeleton__pipeline-step {
    display: flex;
    min-height: 124px;
    max-width: 100%;
    align-items: center;
    gap: 6px;
    padding: 0.5rem 0;
    border-bottom: 0;
  }

  .app-route-dashboard-skeleton__pipeline-step::before {
    content: none;
  }

  .app-route-dashboard-skeleton__pipeline-step i {
    width: 48px;
    height: 48px;
    margin-bottom: 0.25rem;
    border-radius: 8px;
  }

  .app-route-dashboard-skeleton__pipeline-step b,
  .app-route-dashboard-skeleton__pipeline-step em,
  .app-route-dashboard-skeleton__pipeline-step small {
    width: 100%;
    height: 38px;
    margin: 0;
    border-radius: 6px;
  }

  .app-route-dashboard-skeleton__pipeline-connector {
    display: block;
    align-self: start;
    width: 100%;
    min-width: 0;
    height: 1px;
    margin: 32px 0 0;
  }
}

</style>

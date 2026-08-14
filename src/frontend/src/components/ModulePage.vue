<script setup lang="ts">
import { ref, computed, onMounted, useSlots, watch, type Component } from 'vue'
import { useRoute } from 'vue-router'
const slots = useSlots()
import Sidebar from './Sidebar.vue'

export interface SideItem {
  to: string
  label: string
}

export interface MenuItem {
  label: string
  icon?: Component
  to?: string
  pageTitle?: string
  children?: MenuItem[]
}

const props = withDefaults(
  defineProps<{
    title?: string
    pageTitleOverride?: string
    side?: SideItem[]
    menus?: MenuItem[]
    adminHub?: boolean
    bodyFlush?: boolean
    bodyFill?: boolean
    hidePageTitle?: boolean
  }>(),
  {
    title: '',
    pageTitleOverride: '',
    side: () => [],
    menus: () => [],
    adminHub: false,
    bodyFlush: false,
    bodyFill: false,
    hidePageTitle: false,
  },
)

const route = useRoute()
const mainContentRef = ref<HTMLElement | null>(null)
const pageBodyRef = ref<HTMLElement | null>(null)

const isAdminSettingsShell = computed(
  () => props.adminHub || route.path.startsWith('/settings') || route.path.startsWith('/account'),
)

const collapsed = ref(localStorage.getItem('sidebar-collapsed') === 'true')
const effectiveCollapsed = computed(() => collapsed.value)

// Convert side items to menu format if side prop is provided
const menuItems = computed<MenuItem[]>(() => {
  if (props.menus && props.menus.length > 0) {
    return props.menus
  }
  // Convert flat side items to menu format
  return props.side.map((item) => ({
    label: item.label,
    to: item.to,
  }))
})

function stripQuery(to: string) {
  return to.split('?')[0] || to
}

function parseMenuTo(to: string): { path: string; query: Record<string, string> } {
  const [path, qs] = to.split('?')
  const query: Record<string, string> = {}
  if (qs) {
    new URLSearchParams(qs).forEach((value, key) => {
      query[key] = value
    })
  }
  return { path: path || to, query }
}

function menuLinkMatchesRoute(path: string, routeQuery: Record<string, unknown>, to: string) {
  const { path: itemPath, query: itemQuery } = parseMenuTo(to)
  if (path !== itemPath && !path.startsWith(`${itemPath}/`)) return false
  const keys = Object.keys(itemQuery)
  if (keys.length === 0) return true
  return keys.every((key) => String(routeQuery[key] ?? '') === itemQuery[key])
}

function flattenMenuLinks(items: MenuItem[]): { to: string; label: string; pageTitle?: string }[] {
  const out: { to: string; label: string; pageTitle?: string }[] = []
  for (const m of items) {
    if (m.to) out.push({ to: m.to, label: m.label, pageTitle: m.pageTitle })
    if (m.children?.length) out.push(...flattenMenuLinks(m.children))
  }
  return out
}

const displayTitle = computed(() => {
  const override = props.pageTitleOverride?.trim()
  if (override) return override

  const path = route.path
  const links = flattenMenuLinks(menuItems.value).filter((x) => x.to)
  const matches = links.filter((x) => menuLinkMatchesRoute(path, route.query, x.to))
  if (matches.length > 0) {
    const best = matches.reduce((a, b) => (stripQuery(a.to).length >= stripQuery(b.to).length ? a : b))
    return best.pageTitle ?? best.label
  }
  return props.title
})

function toggleSidebar() {
  collapsed.value = !collapsed.value
  localStorage.setItem('sidebar-collapsed', String(collapsed.value))
}

function resetScrollPosition() {
  if (mainContentRef.value) mainContentRef.value.scrollTop = 0
  if (pageBodyRef.value) pageBodyRef.value.scrollTop = 0
  if (document.scrollingElement) document.scrollingElement.scrollTop = 0
}

onMounted(resetScrollPosition)

watch(
  () => route.fullPath,
  resetScrollPosition,
  { flush: 'post' },
)
</script>

<template>
  <div
    class="module-page"
    :class="{ 'module-page--admin': isAdminSettingsShell }"
  >
    <div class="sidebar-wrapper">
      <Sidebar
        v-if="menuItems.length > 0"
        :collapsed="effectiveCollapsed"
        :menus="menuItems"
        @toggle="toggleSidebar"
      >
        <template
          v-if="slots.sidebarPrepend"
          #prepend
        >
          <slot name="sidebar-prepend" />
        </template>
      </Sidebar>
    </div>
    <div
      ref="mainContentRef"
      :class="{ 'sidebar-collapsed': effectiveCollapsed || menuItems.length === 0 }"
      class="main-content"
    >
      <div class="main-content-route-surface">
        <div
          v-if="!hidePageTitle"
          class="page-header"
        >
          <div class="page-title-row">
            <h1 class="page-title">
              {{ displayTitle }}
            </h1>
          </div>
        </div>
        <div
          ref="pageBodyRef"
          class="page-body"
          :class="{ 'page-body--fill': bodyFill }"
        >
          <div
            v-if="slots.actions"
            class="page-actions"
          >
            <slot name="actions" />
          </div>
          <slot />
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sidebar-wrapper {
  position: sticky;
  top: var(--app-header-height);
  display: flex;
  height: calc(var(--app-viewport-height) - var(--app-header-height));
  flex-shrink: 0;
}

@media (max-width: 1023.98px) {
  .sidebar-wrapper {
    display: none;
  }
}
</style>

<style>
.module-page--admin .main-content {
  min-height: calc(var(--app-viewport-height) - var(--app-header-height));
  background-color: var(--content-bg, #111116);
}
</style>

<script setup lang="ts">
import { computed, ref, watch, type Component } from 'vue'
import { RouterLink, useRoute, useRouter } from 'vue-router'
import { ChevronLeft, ChevronRight, HelpCircle } from 'lucide-vue-next'
import { ElTooltip } from 'element-plus'
import { beginRouteRequestScope } from '../lib/routeRequestAbort'
import { beginRouteTransition } from '../lib/routeTransition'

interface MenuItem {
  label: string
  icon?: Component
  to?: string
  pageTitle?: string
  children?: MenuItem[]
}

type FlatNavItem = {
  to?: string
  label: string
  icon?: Component
  groupIndex: number
}

const props = withDefaults(
  defineProps<{
    collapsed?: boolean
    menus: MenuItem[]
  }>(),
  {
    collapsed: false,
  },
)

const emit = defineEmits<{
  toggle: []
}>()

const route = useRoute()
const router = useRouter()

const expandedParentIndices = ref<Set<number>>(new Set())

function indicesOfParentsWithChildren(): number[] {
  return props.menus
    .map((m, i) => (m.children && m.children.length > 0 ? i : -1))
    .filter((i) => i >= 0)
}

function expandAllParentGroups() {
  expandedParentIndices.value = new Set(indicesOfParentsWithChildren())
}

watch(
  () => props.menus.length,
  (len) => {
    if (len > 0) expandAllParentGroups()
  },
  { immediate: true },
)

function toggleExpand(parentIndex: number) {
  const next = new Set(expandedParentIndices.value)
  if (next.has(parentIndex)) next.delete(parentIndex)
  else next.add(parentIndex)
  expandedParentIndices.value = next
}

function routeQueryMatches(key: string, expected: string) {
  const actual = route.query[key]
  if (Array.isArray(actual)) return actual.includes(expected)
  return (
    actual === expected ||
    (key === 'tab' && expected === 's3' && actual == null) ||
    (key === 'tab' && expected === 'backup' && (actual == null || actual === 'backup')) ||
    (key === 'tab' &&
      expected === 'host' &&
      (actual == null || actual === 'host' || actual === 'hostFileSystem'))
  )
}

function isActive(to?: string) {
  if (!to) return false
  const [targetPath, targetQuery = ''] = to.split('?')
  if (targetQuery) {
    if (route.path !== targetPath) return false
    const params = new URLSearchParams(targetQuery)
    for (const [key, expected] of params) {
      if (!routeQueryMatches(key, expected)) return false
    }
    return true
  }
  if (targetPath === '/platform-ops') {
    return route.path === '/platform-ops' || route.path === '/platform-ops/'
  }
  if (route.path === targetPath) return true
  if (route.path.startsWith(`${targetPath}/`)) {
    // Don't highlight if a more specific menu item is a better match
    for (const otherPath of allMenuPaths.value) {
      if (
        otherPath !== targetPath &&
        otherPath.startsWith(`${targetPath}/`) &&
        (route.path === otherPath || route.path.startsWith(`${otherPath}/`))
      ) {
        return false
      }
    }
    return true
  }
  return false
}

const allMenuPaths = computed(() => {
  const paths = new Set<string>()
  function collect(items: MenuItem[]) {
    for (const item of items) {
      if (item.to) paths.add(item.to.split('?')[0])
      if (item.children) collect(item.children)
    }
  }
  collect(props.menus)
  return paths
})

function navigateImmediately(to?: string) {
  if (!to || to === route.fullPath) return
  beginRouteTransition()
  beginRouteRequestScope()
  void router.push(to)
}

function handleNavClick(event: MouseEvent, to?: string) {
  if (!to) return
  if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
  event.preventDefault()
  navigateImmediately(to)
}

const collapsedNavItems = computed<FlatNavItem[]>(() => {
  const out: FlatNavItem[] = []
  props.menus.forEach((item, groupIndex) => {
    if (item.children?.length) {
      for (const child of item.children) {
        out.push({
          to: child.to,
          label: child.label,
          icon: child.icon,
          groupIndex,
        })
      }
    } else if (item.to) {
      out.push({
        to: item.to,
        label: item.label,
        icon: item.icon,
        groupIndex,
      })
    }
  })
  return out
})
</script>

<template>
  <aside
    :class="{ collapsed }"
    class="sidebar"
  >
    <div class="sidebar-content">
      <div
        v-if="$slots.prepend && !collapsed"
        class="sidebar-prepend"
      >
        <slot name="prepend" />
      </div>

      <nav
        v-if="collapsed"
        class="sidebar-nav sidebar-nav--collapsed"
      >
        <template
          v-for="(nav, idx) in collapsedNavItems"
          :key="nav.to || nav.label"
        >
          <div
            v-if="idx > 0 && collapsedNavItems[idx - 1].groupIndex !== nav.groupIndex"
            class="collapsed-divider"
          />
          <ElTooltip
            v-if="nav.to"
            placement="right"
            :content="nav.label"
            :show-after="100"
          >
            <RouterLink
              :to="nav.to"
              :class="{ active: isActive(nav.to) }"
              :aria-label="nav.label"
              class="collapsed-icon-btn"
              @click="handleNavClick($event, nav.to)"
            >
              <component
                :is="nav.icon || HelpCircle"
                :size="18"
                class="collapsed-icon-btn__icon"
              />
            </RouterLink>
          </ElTooltip>
          <ElTooltip
            v-else
            placement="right"
            :content="nav.label"
            :show-after="100"
          >
            <div class="collapsed-icon-btn collapsed-icon-btn--disabled">
              <component
                :is="nav.icon || HelpCircle"
                :size="18"
                class="collapsed-icon-btn__icon"
              />
            </div>
          </ElTooltip>
        </template>
      </nav>

      <nav
        v-else
        class="sidebar-nav"
      >
        <template
          v-for="(item, index) in menus"
          :key="index"
        >
          <div
            v-if="item.children && item.children.length > 0"
            class="menu-item"
          >
            <div
              class="menu-parent"
              :class="{ expanded: expandedParentIndices.has(index) }"
              @click.stop="toggleExpand(index)"
            >
              <span class="menu-label">{{ item.label }}</span>
              <ChevronRight
                :size="14"
                :class="{ expanded: expandedParentIndices.has(index) }"
                class="expand-icon"
              />
            </div>
            <Transition name="slide">
              <div
                v-if="expandedParentIndices.has(index)"
                class="submenu"
              >
                <template
                  v-for="child in item.children"
                  :key="child.to || child.label"
                >
                  <RouterLink
                    v-if="child.to"
                    :to="child.to"
                    :class="{ active: isActive(child.to) }"
                    class="submenu-item"
                    @click="handleNavClick($event, child.to)"
                  >
                    <component
                      :is="child.icon"
                      v-if="child.icon"
                      :size="16"
                      class="submenu-item__icon"
                    />
                    <span class="submenu-item__label">{{ child.label }}</span>
                  </RouterLink>
                  <div
                    v-else
                    class="submenu-item submenu-item--disabled"
                  >
                    <component
                      :is="child.icon"
                      v-if="child.icon"
                      :size="16"
                      class="submenu-item__icon"
                    />
                    <span class="submenu-item__label">{{ child.label }}</span>
                  </div>
                </template>
              </div>
            </Transition>
          </div>

          <RouterLink
            v-else-if="item.to"
            :to="item.to"
            :class="{ active: isActive(item.to) }"
            class="menu-link"
            @click="handleNavClick($event, item.to)"
          >
            <component
              :is="item.icon"
              v-if="item.icon"
              :size="16"
              class="menu-link__icon"
            />
            <span class="menu-link__label">{{ item.label }}</span>
          </RouterLink>
        </template>
      </nav>

      <div
        class="sidebar-footer"
        @click="emit('toggle')"
      >
        <ChevronLeft
          :size="16"
          :class="{ 'rotated': collapsed }"
          class="toggle-icon"
        />
      </div>
    </div>
  </aside>
</template>

<style scoped>
.sidebar {
  --brand-color: var(--color-primary, #6D5EF6);
  --brand-color-light: #A99BFF;
  --brand-glow: rgba(109, 94, 246, 0.28);
  --sidebar-active-bg: rgba(109, 94, 246, 0.19);
  --sidebar-active-color: #FFFFFF;
  --sidebar-subtext: var(--sidebar-subtext-color, #3A3A48);
}

.sidebar {
  width: 238px;
  height: 100%;
  background:
    var(--sidebar-overlay, linear-gradient(180deg, rgba(255, 255, 255, 0.035), rgba(255, 255, 255, 0))),
    var(--sidebar-bg, #FCFCFD);
  border-right: 1px solid var(--sidebar-border, #E9E9EF);
  box-shadow: var(--sidebar-shadow, 8px 0 18px rgba(28, 28, 38, 0.05));
  transition: width 200ms ease;
  overflow: hidden;
  flex-shrink: 0;
  font-family: var(--font-sans);
}

.sidebar.collapsed {
  width: 56px;
}

.sidebar-content {
  display: flex;
  flex-direction: column;
  width: 238px;
  height: 100%;
  overflow: hidden;
  transition: width 200ms ease;
}

.sidebar.collapsed .sidebar-content {
  width: 56px;
}

.sidebar-prepend {
  padding: 16px 12px 8px;
  border-bottom: 0;
  background: transparent;
  color: var(--sidebar-section, #9C9CAA);
  font-size: 12px;
  font-weight: 650;
  letter-spacing: 0;
  text-transform: none;
}

.sidebar-nav {
  flex: 1;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 12px 0 8px;
  color: var(--sidebar-text, #3A3A48);
}

.sidebar-nav--collapsed {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 12px 6px;
}

.collapsed-divider {
  width: 32px;
  height: 1px;
  margin: 10px 0;
  background-color: var(--sidebar-border, #E9E9EF);
  opacity: 0.8;
}

.collapsed-icon-btn {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 42px;
  height: 42px;
  border-radius: 8px;
  color: var(--sidebar-text, #3A3A48);
  text-decoration: none;
  transition: background-color 150ms ease, color 150ms ease, transform 150ms ease;
}

.collapsed-icon-btn__icon {
  flex-shrink: 0;
  opacity: 0.75;
  transition: opacity 150ms ease, transform 150ms ease;
}

.collapsed-icon-btn:hover {
  background-color: var(--sidebar-hover-bg, rgba(109, 94, 246, 0.07));
  color: var(--sidebar-hover-color, #1C1C26);
}

.collapsed-icon-btn:hover .collapsed-icon-btn__icon {
  opacity: 1;
  transform: scale(1.08);
}

.collapsed-icon-btn.active {
  background: var(--sidebar-active-bg, #F2F0FE);
  color: var(--sidebar-active-color, #FFFFFF);
}

.collapsed-icon-btn.active::before {
  content: '';
  position: absolute;
  left: 6px;
  top: 9px;
  transform: none;
  width: 3px;
  height: 24px;
  background: var(--sidebar-active-line-bg, linear-gradient(180deg, var(--brand-color, #6D5EF6), var(--brand-color-light, #A99BFF)));
  border-radius: 999px;
  box-shadow: 0 0 10px var(--brand-glow, rgba(109, 94, 246, 0.28));
}

.collapsed-icon-btn.active .collapsed-icon-btn__icon {
  opacity: 1;
  color: var(--brand-color, #6D5EF6);
}

.menu-item + .menu-item,
.menu-item + .menu-link,
.menu-link + .menu-item {
  margin-top: 20px;
}

.menu-parent {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: space-between;
  min-height: 28px;
  margin: 0 8px 2px;
  padding: 0 10px;
  border-radius: 8px;
  font-size: 11px;
  font-weight: 650;
  letter-spacing: 0.025em;
  color: var(--sidebar-section, var(--color-text-secondary, #9C9CAA));
  cursor: pointer;
  transition: background-color 150ms ease, color 150ms ease;
}

.menu-parent:hover {
  background-color: var(--sidebar-hover-bg, rgba(109, 94, 246, 0.07));
  color: var(--sidebar-section, var(--color-text-secondary, #9C9CAA));
}

.menu-parent.expanded {
  background-image: none;
}

.menu-label {
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.expand-icon {
  color: var(--sidebar-section, var(--color-text-secondary, #9C9CAA));
  opacity: 0.72;
  transition: transform 200ms ease, opacity 150ms ease;
  flex-shrink: 0;
}

.expand-icon.expanded {
  transform: rotate(90deg);
}

.submenu {
  overflow: hidden;
}

.submenu-item {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 36px;
  margin: 2px 8px;
  padding: 0 12px 0 24px;
  border-radius: var(--sidebar-active-radius, 8px);
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--sidebar-subtext, #3A3A48);
  text-decoration: none;
  transition: background-color 150ms ease, color 150ms ease;
}

.submenu-item__icon {
  flex-shrink: 0;
  opacity: 0.82;
}

.submenu-item__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.submenu-item:hover {
  background-color: var(--sidebar-hover-bg, rgba(109, 94, 246, 0.07));
  color: var(--sidebar-hover-color, #1C1C26);
}

.submenu-item:hover .submenu-item__icon {
  opacity: 1;
}

.submenu-item.active {
  background: var(--sidebar-active-bg, linear-gradient(90deg, rgba(109, 94, 246, 0.22), rgba(109, 94, 246, 0.04)));
  color: var(--sidebar-active-color, #FFFFFF);
  font-weight: 650;
  box-shadow: none;
}

.submenu-item.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 7px;
  bottom: 7px;
  width: 3px;
  border-radius: 999px;
  background: var(--sidebar-active-line-bg, #F5A623);
  box-shadow: 0 0 8px rgba(245, 166, 35, 0.24);
}

.submenu-item.active .submenu-item__icon {
  opacity: 1;
  color: currentColor;
}

.submenu-item--disabled {
  cursor: default;
  opacity: 0.85;
}

.submenu-item--disabled:hover {
  background-color: transparent;
  color: var(--sidebar-text, #3A3A48);
}

.collapsed-icon-btn--disabled {
  cursor: default;
  opacity: 0.7;
}

.collapsed-icon-btn--disabled:hover {
  background-color: transparent;
  color: var(--sidebar-text, #3A3A48);
}

.collapsed-icon-btn--disabled:hover .collapsed-icon-btn__icon {
  transform: none;
  opacity: 0.75;
}

.menu-link {
  position: relative;
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 36px;
  margin: 2px 8px;
  padding: 0 12px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 600;
  letter-spacing: 0;
  color: var(--sidebar-text, #3A3A48);
  text-decoration: none;
  transition: background-color 150ms ease, color 150ms ease;
}

.menu-link__icon {
  flex-shrink: 0;
  margin-right: 2px;
  opacity: 0.82;
}

.menu-link__label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.menu-link:hover {
  background-color: var(--sidebar-hover-bg, rgba(109, 94, 246, 0.07));
  color: var(--sidebar-hover-color, #1C1C26);
}

.menu-link:hover .menu-link__icon {
  opacity: 1;
}

.menu-link.active {
  background: var(--sidebar-active-bg, linear-gradient(90deg, rgba(109, 94, 246, 0.22), rgba(109, 94, 246, 0.04)));
  color: var(--sidebar-active-color, #FFFFFF);
  font-weight: 650;
  box-shadow: none;
}

.menu-link.active::before {
  content: '';
  position: absolute;
  left: 0;
  top: 7px;
  bottom: 7px;
  width: 3px;
  border-radius: 999px;
  background: var(--sidebar-active-line-bg, #F5A623);
  box-shadow: 0 0 8px rgba(245, 166, 35, 0.24);
}

.menu-link.active .menu-link__icon {
  opacity: 1;
  color: currentColor;
}

.slide-enter-active,
.slide-leave-active {
  transition: all 200ms ease;
  overflow: hidden;
}

.slide-enter-from,
.slide-leave-to {
  opacity: 0;
  max-height: 0;
}

.slide-enter-to,
.slide-leave-from {
  opacity: 1;
  max-height: 500px;
}

.sidebar-footer {
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  height: 40px;
  border-top: 1px solid rgba(109, 94, 246, 0.16);
  background: rgba(255, 255, 255, 0.015);
  cursor: pointer;
  color: var(--sidebar-section, #9C9CAA);
  transition: background-color 150ms ease, color 150ms ease;
}

.sidebar-footer:hover {
  background-color: var(--sidebar-hover-bg, rgba(109, 94, 246, 0.07));
  color: var(--brand-color, #6D5EF6);
}

.toggle-icon {
  transition: transform 200ms ease;
  color: var(--brand-color, #6D5EF6);
}

.toggle-icon.rotated {
  transform: rotate(180deg);
}

.menu-link__icon,
.submenu-item__icon {
  color: currentColor;
  opacity: 0.76;
}
</style>

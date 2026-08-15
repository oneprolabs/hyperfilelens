<script setup lang="ts">
import { ref, watch } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import { ChevronDown, Clock3, ExternalLink, Shield, X } from 'lucide-vue-next'
import type { AppPrimaryNavItem } from '../composables/useAppPrimaryNav'
import type { MenuItem } from './ModulePage.vue'
import OrgSwitcher from './OrgSwitcher.vue'
import LanguageSwitcher from './LanguageSwitcher.vue'

const props = withDefaults(
  defineProps<{
    modelValue: boolean
    title: string
    primaryItems?: AppPrimaryNavItem[]
    moduleItems?: MenuItem[]
    adminConsoleHref?: string
    showOrganizationSwitcher?: boolean
    timezoneOffsetDisplay?: string
  }>(),
  {
    primaryItems: () => [],
    moduleItems: () => [],
    adminConsoleHref: '',
    timezoneOffsetDisplay: '',
  },
)

const emit = defineEmits<{
  'update:modelValue': [value: boolean]
}>()

const route = useRoute()
const expandedGroups = ref(new Set<number>())

watch(
  () => props.moduleItems,
  (items) => {
    expandedGroups.value = new Set(
      items.map((item, index) => (item.children?.length ? index : -1)).filter((index) => index >= 0),
    )
  },
  { immediate: true },
)

watch(
  () => route.fullPath,
  () => close(),
)

function close() {
  emit('update:modelValue', false)
}

function toggleGroup(index: number) {
  const next = new Set(expandedGroups.value)
  if (next.has(index)) next.delete(index)
  else next.add(index)
  expandedGroups.value = next
}

function routeQueryMatches(key: string, expected: string) {
  const actual = route.query[key]
  if (Array.isArray(actual)) return actual.includes(expected)
  return (
    actual === expected ||
    (key === 'tab' && expected === 's3' && actual == null) ||
    (key === 'tab' && expected === 'backup' && (actual == null || actual === 'backup')) ||
    (key === 'tab' && expected === 'host' && (actual == null || actual === 'host' || actual === 'hostFileSystem'))
  )
}

function moduleItemActive(to?: string) {
  if (!to) return false
  const [targetPath, targetQuery = ''] = to.split('?')
  if (route.path !== targetPath && !route.path.startsWith(`${targetPath}/`)) return false
  if (!targetQuery) return true
  const params = new URLSearchParams(targetQuery)
  for (const [key, expected] of params) {
    if (!routeQueryMatches(key, expected)) return false
  }
  return true
}
</script>

<template>
  <ElDrawer
    :model-value="modelValue"
    class="hfl-mobile-navigation"
    direction="ltr"
    size="min(88vw, 360px)"
    append-to-body
    :show-close="false"
    :with-header="false"
    :close-on-press-escape="true"
    :close-on-click-modal="true"
    @update:model-value="emit('update:modelValue', $event)"
  >
    <div class="mobile-navigation__shell">
      <header class="mobile-navigation__header">
        <h2>{{ title }}</h2>
        <button
          type="button"
          class="mobile-navigation__close"
          :aria-label="$t('common.close')"
          @click="close"
        >
          <X
            :size="20"
            aria-hidden="true"
          />
        </button>
      </header>

      <nav
        class="mobile-navigation__body"
        :aria-label="title"
      >
        <section
          v-if="showOrganizationSwitcher"
          class="mobile-navigation__section"
        >
          <OrgSwitcher variant="mobile" />
        </section>

        <section
          v-if="primaryItems.length"
          class="mobile-navigation__section"
        >
          <RouterLink
            v-for="item in primaryItems"
            :key="item.to"
            :to="item.to"
            class="mobile-navigation__primary-link"
            :class="{ 'is-active': item.active }"
            :aria-current="item.active ? 'page' : undefined"
            @click="close"
          >
            <component
              :is="item.icon"
              :size="17"
              :stroke-width="2"
              aria-hidden="true"
            />
            <span>{{ item.label }}</span>
          </RouterLink>
        </section>

        <section
          v-if="moduleItems.length"
          class="mobile-navigation__section mobile-navigation__section--module"
        >
          <template
            v-for="(item, index) in moduleItems"
            :key="item.to || `${item.label}-${index}`"
          >
            <button
              v-if="item.children?.length"
              type="button"
              class="mobile-navigation__group-button"
              :aria-expanded="expandedGroups.has(index)"
              @click="toggleGroup(index)"
            >
              <span>{{ item.label }}</span>
              <ChevronDown
                :size="17"
                :class="{ 'is-expanded': expandedGroups.has(index) }"
                aria-hidden="true"
              />
            </button>
            <div
              v-if="item.children?.length && expandedGroups.has(index)"
              class="mobile-navigation__children"
            >
              <RouterLink
                v-for="child in item.children"
                :key="child.to || child.label"
                :to="child.to || route.fullPath"
                class="mobile-navigation__module-link"
                :class="{ 'is-active': moduleItemActive(child.to), 'is-disabled': !child.to }"
                :aria-current="moduleItemActive(child.to) ? 'page' : undefined"
                :aria-disabled="!child.to || undefined"
                @click="child.to && close()"
              >
                <component
                  :is="child.icon"
                  v-if="child.icon"
                  :size="17"
                  aria-hidden="true"
                />
                <span>{{ child.label }}</span>
              </RouterLink>
            </div>
            <RouterLink
              v-else-if="item.to"
              :to="item.to"
              class="mobile-navigation__module-link"
              :class="{ 'is-active': moduleItemActive(item.to) }"
              :aria-current="moduleItemActive(item.to) ? 'page' : undefined"
              @click="close"
            >
              <component
                :is="item.icon"
                v-if="item.icon"
                :size="17"
                aria-hidden="true"
              />
              <span>{{ item.label }}</span>
            </RouterLink>
          </template>
        </section>

        <section
          v-if="adminConsoleHref"
          class="mobile-navigation__section mobile-navigation__section--utility"
        >
          <a
            :href="adminConsoleHref"
            class="mobile-navigation__utility-link"
            target="_blank"
            rel="noopener noreferrer"
            @click="close"
          >
            <Shield
              :size="17"
              aria-hidden="true"
            />
            <span>{{ $t('nav.platformOps') }}</span>
            <ExternalLink
              class="mobile-navigation__utility-external"
              :size="15"
              aria-hidden="true"
            />
          </a>
        </section>

        <section class="mobile-navigation__section mobile-navigation__section--utility">
          <LanguageSwitcher
            variant="mobile"
            @change="close"
          />
          <div
            v-if="timezoneOffsetDisplay"
            class="mobile-navigation__utility-link mobile-navigation__utility-link--static"
          >
            <Clock3
              :size="17"
              aria-hidden="true"
            />
            <span class="mobile-navigation__utility-copy">
              <span class="mobile-navigation__utility-title">{{ $t('nav.timezoneLabel') }}</span>
              <span class="mobile-navigation__utility-subtitle">{{ timezoneOffsetDisplay }}</span>
            </span>
          </div>
        </section>
      </nav>
    </div>
  </ElDrawer>
</template>

<style scoped>
:global(.hfl-mobile-navigation) {
  max-width: calc(100vw - var(--app-safe-right));
}

:global(.hfl-mobile-navigation .el-drawer__body) {
  padding: 0 !important;
}

.mobile-navigation__shell {
  display: flex;
  height: 100%;
  min-height: 0;
  flex-direction: column;
  background: var(--sidebar-bg, var(--el-bg-color));
  color: var(--sidebar-text, var(--el-text-color-primary));
}

.mobile-navigation__header {
  display: flex;
  min-height: calc(56px + var(--app-safe-top));
  flex: 0 0 auto;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  padding: calc(10px + var(--app-safe-top)) 12px 10px calc(16px + var(--app-safe-left));
  border-bottom: 1px solid var(--sidebar-border, var(--el-border-color-lighter));
}

.mobile-navigation__header h2 {
  min-width: 0;
  overflow: hidden;
  margin: 0;
  color: inherit;
  font-size: 16px;
  font-weight: 650;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mobile-navigation__close {
  display: inline-flex;
  width: 44px;
  height: 44px;
  flex: 0 0 44px;
  align-items: center;
  justify-content: center;
  padding: 0;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: inherit;
  cursor: pointer;
}

.mobile-navigation__close:hover,
.mobile-navigation__close:focus-visible {
  background: var(--el-fill-color-light);
}

.mobile-navigation__body {
  min-height: 0;
  flex: 1 1 auto;
  overflow-y: auto;
  padding: 12px max(12px, var(--app-safe-right)) calc(20px + var(--app-safe-bottom)) max(12px, var(--app-safe-left));
}

.mobile-navigation__section {
  display: grid;
  gap: 4px;
}

.mobile-navigation__section + .mobile-navigation__section {
  margin-top: 16px;
  padding-top: 16px;
  border-top: 1px solid var(--sidebar-border, var(--el-border-color-lighter));
}

.mobile-navigation__primary-link,
.mobile-navigation__module-link,
.mobile-navigation__group-button,
.mobile-navigation__utility-link {
  display: flex;
  min-height: 44px;
  align-items: center;
  gap: 10px;
  width: 100%;
  box-sizing: border-box;
  padding: 9px 12px;
  border: 0;
  border-radius: 9px;
  background: transparent;
  color: inherit;
  font: inherit;
  font-size: 14px;
  text-align: left;
  text-decoration: none;
  cursor: pointer;
}

.mobile-navigation__primary-link:hover,
.mobile-navigation__primary-link:focus-visible,
.mobile-navigation__module-link:hover,
.mobile-navigation__module-link:focus-visible,
.mobile-navigation__group-button:hover,
.mobile-navigation__group-button:focus-visible,
.mobile-navigation__utility-link:hover,
.mobile-navigation__utility-link:focus-visible {
  background: var(--el-fill-color-light);
}

.mobile-navigation__primary-link.is-active,
.mobile-navigation__module-link.is-active {
  background: color-mix(in srgb, var(--color-primary) 14%, transparent);
  color: var(--color-primary);
  font-weight: 650;
}

.mobile-navigation__group-button {
  justify-content: space-between;
  color: var(--el-text-color-secondary);
  font-weight: 650;
}

.mobile-navigation__group-button svg {
  transition: transform 150ms ease;
}

.mobile-navigation__group-button svg.is-expanded {
  transform: rotate(180deg);
}

.mobile-navigation__children {
  display: grid;
  gap: 3px;
  padding-left: 8px;
}

.mobile-navigation__section--utility {
  color: var(--el-text-color-secondary);
}

.mobile-navigation__utility-link {
  color: inherit;
}

.mobile-navigation__utility-link--static {
  cursor: default;
}

.mobile-navigation__utility-link--static:hover {
  background: transparent;
}

.mobile-navigation__utility-copy {
  display: grid;
  min-width: 0;
  gap: 2px;
}

.mobile-navigation__utility-title {
  color: var(--sidebar-text, var(--el-text-color-primary));
  font-weight: 600;
}

.mobile-navigation__utility-subtitle {
  overflow: hidden;
  color: var(--el-text-color-secondary);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.mobile-navigation__utility-external {
  flex: 0 0 auto;
  margin-left: auto;
}

.mobile-navigation__module-link.is-disabled {
  cursor: default;
  opacity: 0.5;
  pointer-events: none;
}
</style>

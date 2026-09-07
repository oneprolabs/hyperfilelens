import { computed, type Component } from 'vue'
import { useI18n } from 'vue-i18n'
import { useRoute } from 'vue-router'
import {
  Activity,
  LayoutDashboard,
  Settings,
  ShieldCheck,
  Sparkles,
} from 'lucide-vue-next'

export interface AppPrimaryNavItem {
  to: string
  label: string
  icon: Component
  active?: boolean
}

function pathMatchesPrefix(path: string, prefix: string) {
  return path === prefix || path.startsWith(`${prefix}/`)
}

const protectionRoutePrefixes = [
  '/protection/backup-sources',
  '/node/agents',
  '/node/repositories',
  '/node/snapshots',
  '/node/deployment',
]

const insightRoutePrefixes = ['/node/ai-settings', '/node/knowledge-base', '/node/gateways']

export function useAppPrimaryNav() {
  const { t } = useI18n()
  const route = useRoute()

  const items = computed<AppPrimaryNavItem[]>(() => [
    { to: '/', label: t('nav.overview'), icon: LayoutDashboard },
    { to: '/protection', label: t('nav.protection'), icon: ShieldCheck },
    { to: '/insight', label: t('nav.insight'), icon: Sparkles },
    { to: '/node', label: t('nav.node'), icon: Settings },
    { to: '/ops', label: t('nav.ops'), icon: Activity },
  ])

  function isActive(to: string) {
    const path = route.path
    if (to === '/') return path === '/'

    if (to === '/protection') {
      if (pathMatchesPrefix(path, to)) return true
      return protectionRoutePrefixes.some((prefix) => pathMatchesPrefix(path, prefix))
    }

    if (to === '/insight') {
      if (pathMatchesPrefix(path, to)) return true
      return insightRoutePrefixes.some((prefix) => pathMatchesPrefix(path, prefix))
    }

    if (to === '/node') {
      if (protectionRoutePrefixes.some((prefix) => pathMatchesPrefix(path, prefix))) return false
      if (insightRoutePrefixes.some((prefix) => pathMatchesPrefix(path, prefix))) return false
      return pathMatchesPrefix(path, to)
    }

    return pathMatchesPrefix(path, to)
  }

  const itemsWithActiveState = computed(() =>
    items.value.map((item) => ({ ...item, active: isActive(item.to) })),
  )

  return {
    items,
    itemsWithActiveState,
    isActive,
  }
}

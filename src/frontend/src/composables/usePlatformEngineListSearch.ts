import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'

/** Admin AI Engine list search: match Governance/Platform size + "Search X" placeholder. */
export function usePlatformEngineListSearch(
  tenantPlaceholderKey: string,
  directorySearchKey:
    | 'models'
    | 'gateways'
    | 'knowledgeSources'
    | 'assistants'
    | 'skills'
    | 'mcpServers'
    | 'chats'
    | 'usage',
) {
  const route = useRoute()
  const { t } = useI18n()
  const isPlatformEngine = computed(() => route.path.startsWith('/platform-ops/engine'))
  const listSearchPlaceholder = computed(() => (
    isPlatformEngine.value
      ? t(`platformOps.directorySearch.${directorySearchKey}`)
      : t(tenantPlaceholderKey)
  ))
  const listSearchClass = computed(() => (
    isPlatformEngine.value ? 'hfl-list-search platform-list-search' : 'hfl-list-search'
  ))
  const listSearchSize = computed(() => (isPlatformEngine.value ? undefined : 'small'))

  return {
    isPlatformEngine,
    listSearchPlaceholder,
    listSearchClass,
    listSearchSize,
  }
}

import { ref } from 'vue'
import { getNode, listNodesPaged } from '../lib/nodeApi'
import type { ApiNode } from '../types/node'
import { usePageRequestScope } from './usePageRequestScope'

const PROXY_PAGE_SIZE = 50

export function useProxyNodeOptions(selectedId: () => number | undefined, onlineOnly = false) {
  const proxyNodes = ref<ApiNode[]>([])
  const proxyOptionsLoading = ref(false)
  const requests = usePageRequestScope()
  let currentPage = 0
  let currentSearch = ''
  let hasMore = false
  let loadingMore = false

  function fetchPage(page: number, signal: AbortSignal) {
    return listNodesPaged({
      role: 'proxy', availability: onlineOnly ? 'online' : undefined,
      page, page_size: PROXY_PAGE_SIZE, search: currentSearch,
    }, { signal })
  }

  async function loadProxyNodes(search = '') {
    const signal = requests.nextSignal('proxy-options')
    currentSearch = search.trim()
    currentPage = 0
    hasMore = false
    loadingMore = false
    proxyOptionsLoading.value = true
    try {
      const result = await fetchPage(1, signal)
      if (signal.aborted) return
      const id = selectedId()
      const selected = id && !result.results.some((node) => node.id === id)
        ? await getNode(id, { signal }).catch(() => null)
        : null
      if (signal.aborted) return
      proxyNodes.value = selected?.role === 'proxy' ? [selected, ...result.results] : result.results
      currentPage = 1
      hasMore = result.count > PROXY_PAGE_SIZE
    } finally {
      requests.releaseSignal('proxy-options', signal)
      if (!signal.aborted) proxyOptionsLoading.value = false
    }
  }

  async function loadMoreProxyNodes(direction: string) {
    if (direction !== 'bottom' || !hasMore || proxyOptionsLoading.value || loadingMore) return
    const signal = requests.nextSignal('proxy-options')
    const page = currentPage + 1
    loadingMore = true
    try {
      const result = await fetchPage(page, signal)
      if (signal.aborted) return
      const existing = new Map(proxyNodes.value.map((node) => [node.id, node]))
      for (const node of result.results) existing.set(node.id, node)
      proxyNodes.value = [...existing.values()]
      currentPage = page
      hasMore = result.results.length > 0 && result.count > page * PROXY_PAGE_SIZE
    } catch {
      return
    } finally {
      requests.releaseSignal('proxy-options', signal)
      if (!signal.aborted) loadingMore = false
    }
  }

  function searchProxyNodes(search: string) {
    void loadProxyNodes(search).catch(() => undefined)
  }

  return { proxyNodes, proxyOptionsLoading, loadProxyNodes, searchProxyNodes, loadMoreProxyNodes }
}

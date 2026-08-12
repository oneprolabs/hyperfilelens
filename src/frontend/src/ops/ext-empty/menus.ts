import type { MenuItem } from '../../components/ModulePage.vue'

/** Community build: no Enterprise infrastructure monitoring menu. */
export function tenantOpsObserveMenus(_t: (key: string) => string): MenuItem[] {
  return []
}

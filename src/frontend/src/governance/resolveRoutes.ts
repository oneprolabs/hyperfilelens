import { governanceRoutes as extGovernanceRoutes } from '@ext/platform/governance/routes'
import { governanceRoutes as hostGovernanceRoutes } from './routes'

type RouteLike = { path?: string }

/** EE wins on conflicts; Host keeps the stable organization pages available. */
export function resolveGovernanceRoutes() {
  const extensionRoutes = (extGovernanceRoutes || []) as RouteLike[]
  const merged = [...extensionRoutes]
  for (const route of hostGovernanceRoutes as RouteLike[]) {
    if (!route.path || merged.some((item) => item.path === route.path)) continue
    merged.push(route)
  }
  return merged
}

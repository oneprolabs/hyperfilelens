import { resolveSafeLoginRedirect } from './loginNavigation'
import { prepareTabOrganizationContext } from './organizationStorage'

/** Leave context unchanged until the new document verifies the target organization. */
export function completeOrganizationSwitch(orgKey: string, landingPath: string): void {
  const path = resolveSafeLoginRedirect(landingPath, window.location.origin)
  if (!orgKey || !path || /^\/(?:platform-ops|admin)(?:\/|$)/i.test(path.split(/[?#]/)[0])) {
    throw new Error('Invalid organization switch destination.')
  }
  prepareTabOrganizationContext()
  const target = new URL(path, window.location.origin)
  target.searchParams.set('org', orgKey)
  // The existing auth guard validates ?org= on the new document before mounting
  // the tenant page. beforeunload cancellation must not change this document's
  // request headers, access profile, or in-flight request scope.
  window.location.assign(`${target.pathname}${target.search}${target.hash}`)
}

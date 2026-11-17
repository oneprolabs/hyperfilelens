import { freezeOrganizationContext, releaseOrganizationContext } from './organizationStorage'

/** Re-enter auth/bootstrap instead of resuming stale tenant state from BFCache. */
export function installOrganizationHistoryGuard(): () => void {
  let restoring = false
  const onPageHide = (event: PageTransitionEvent) => {
    if (event.persisted) freezeOrganizationContext()
  }
  const onPageShow = (event: PageTransitionEvent) => {
    if (!event.persisted || restoring) return
    restoring = true
    // pagehide pinned the old document's scope. Keep it pinned until teardown,
    // including while navigation waits on the network or resumed timers fire.
    freezeOrganizationContext()
    window.location.reload()
  }
  window.addEventListener('pagehide', onPageHide, true)
  window.addEventListener('pageshow', onPageShow, true)
  return () => {
    window.removeEventListener('pagehide', onPageHide, true)
    window.removeEventListener('pageshow', onPageShow, true)
    releaseOrganizationContext()
  }
}

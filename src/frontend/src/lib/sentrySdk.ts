import {
  browserTracingIntegration as createBrowserTracingIntegration,
  init as initializeSentry,
} from '@sentry/vue'

// Keep a real runtime module at the dynamic-import boundary. A pure re-export
// facade is collapsed by Rollup and can turn the optional SDK back into a
// static entry dependency.
export const browserTracingIntegration = (
  ...args: Parameters<typeof createBrowserTracingIntegration>
) => createBrowserTracingIntegration(...args)

export const init = (
  ...args: Parameters<typeof initializeSentry>
) => initializeSentry(...args)

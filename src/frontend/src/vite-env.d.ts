/// <reference types="vite/client" />

declare const __HFL_EXTENSIONS_FRONTEND__: boolean

declare module '@ext/platform/platform-ops/routes' {
  export const platformOpsRoutes: Array<Record<string, unknown>>
}

declare module '@ext/platform/platform-ops/composables/usePlatformOpsSideNav' {
  import type { ComputedRef } from 'vue'
  export function usePlatformOpsSideNav(): ComputedRef<unknown[]> | null
}

declare module '@ext/platform/platform-ops/runtimeServiceConnections' {
  import type { Component } from 'vue'
  export const RuntimeServiceConnections: Component
}

declare module '@ext/platform/ops/routes' {
  export const tenantOpsRoutes: Array<Record<string, unknown>>
}

declare module '@ext/platform/ops/menus' {
  export function tenantOpsObserveMenus(t: (key: string) => string): Array<{
    label: string
    to?: string
    icon?: unknown
    children?: unknown[]
  }>
}

interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_SHOW_EULA?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

interface HFLAppRuntimeConfig {
  gaMeasurementId?: string
  sentryEnabled?: boolean
  sentryDsn?: string
  sentryEnvironment?: string
  sentryRelease?: string
  sentryTracesSampleRate?: number
  sentrySurface?: 'tenant' | 'admin'
}

interface Window {
  __HFL_APP_CONFIG__?: HFLAppRuntimeConfig
}

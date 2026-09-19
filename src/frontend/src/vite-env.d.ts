/// <reference types="vite/client" />

declare const __HFL_EXTENSIONS_FRONTEND__: boolean

declare module 'docx-preview' {
  export function renderAsync(...args: unknown[]): Promise<void>
}

declare module 'pptx-preview' {
  export function init(...args: unknown[]): {
    preview: (buffer: ArrayBuffer) => Promise<void>
    destroy?: () => void
    renderNextSlide?: () => void
    renderPreSlide?: () => void
  }
}

declare module '@ext/platform/platform-ops/routes' {
  export const platformOpsRoutes: Array<Record<string, unknown>>
}

declare module '@ext/platform/platform-ops/composables/usePlatformOpsSideNav' {
  import type { ComputedRef } from 'vue'
  export function usePlatformOpsSideNav(): ComputedRef<unknown[]> | null
}

declare module '@ext/platform/ops/routes' {
  export const tenantOpsRoutes: Array<Record<string, unknown>>
}

declare module '@ext/platform/governance/routes' {
  export const governanceRoutes: Array<Record<string, unknown>>
}

declare module '@ext/platform/governance/menu' {
  export const governanceMenuItems: Array<{
    id: string
    labelKey: string
    to: string
    icon: 'organization' | 'members' | 'roles' | 'resources'
    requiredRoles?: readonly string[]
  }>
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

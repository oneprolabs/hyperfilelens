import { lazyRoute } from '../router/lazyRoute'

/**
 * Community / OSS Platform Ops routes — AI Models (+ Runtime) shell.
 * Email / identity / environment page components stay here so the platform
 * extension can merge them into the full ops console. Data Gateways live in EE.
 */
export const platformOpsRoutes = [
  { path: '', redirect: '/platform-ops/engine/ai-settings' },
  {
    path: 'platform/email',
    name: 'PlatformOpsSettingsEmail',
    meta: { analytics: { pageKey: 'platform.email', pageGroup: 'platform', pageSurface: 'admin', titleKey: 'platformOps.settings.emailTitle' } },
    component: lazyRoute(() => import('./pages/platform/settings/EmailSettings.vue')),
  },
  {
    path: 'platform/authentication',
    name: 'PlatformOpsAuthentication',
    meta: { analytics: { pageKey: 'platform.authentication', pageGroup: 'platform', pageSurface: 'admin', titleKey: 'platformOps.settings.identityTitle' } },
    component: lazyRoute(() => import('./pages/platform/settings/IdentitySettings.vue')),
  },
  {
    path: 'platform/runtime-environment',
    name: 'PlatformOpsRuntimeEnvironment',
    meta: { analytics: { pageKey: 'platform.runtime_environment', pageGroup: 'platform', pageSurface: 'admin', titleKey: 'platformOps.settings.environmentTitle' } },
    component: lazyRoute(() => import('./pages/platform/settings/EnvironmentSettings.vue')),
  },
  { path: 'platform/settings/email', redirect: '/platform-ops/platform/email' },
  { path: 'platform/settings/turnstile', redirect: '/platform-ops/platform/authentication' },
  { path: 'platform/settings/google-oauth', redirect: '/platform-ops/platform/authentication' },
  { path: 'platform/settings/identity', redirect: '/platform-ops/platform/authentication' },
  { path: 'platform/settings/environment', redirect: '/platform-ops/platform/runtime-environment' },
  {
    path: 'engine',
    component: () => import('./layout/PlatformEngineLayout.vue'),
    children: [
      { path: '', redirect: '/platform-ops/engine/ai-settings' },
      {
        path: 'ai-settings',
        meta: { analytics: { pageKey: 'platform.ai_models', pageGroup: 'platform', pageSurface: 'admin', titleKey: 'platformOps.nav.engineModels' } },
        component: lazyRoute(() => import('../pages/insight/InsightAiSettings.vue')),
      },
      {
        path: 'ai-settings/add',
        meta: { analytics: { pageKey: 'platform.ai_models', pageGroup: 'platform', pageSurface: 'admin', titleKey: 'platformOps.nav.engineModels' } },
        component: lazyRoute(() => import('../pages/insight/AiModelFormPage.vue')),
      },
      {
        path: 'ai-settings/:uuid/edit',
        meta: { analytics: { pageKey: 'platform.ai_models', pageGroup: 'platform', pageSurface: 'admin', titleKey: 'platformOps.nav.engineModels' } },
        component: lazyRoute(() => import('../pages/insight/AiModelFormPage.vue')),
      },
      // Community bookmarks for the removed gateways menu → AI Models.
      { path: 'gateways', redirect: '/platform-ops/engine/ai-settings' },
      { path: 'gateways/add', redirect: '/platform-ops/engine/ai-settings' },
    ],
  },
]

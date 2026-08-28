import { nextTick } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import { resolvePlatformOpsRoutes } from '../platform-ops/resolveRoutes'
import { resolveTenantOpsRoutes } from '../ops/resolveRoutes'

const platformOpsRoutes = resolvePlatformOpsRoutes()
const tenantOpsRoutes = resolveTenantOpsRoutes()
import { beginRouteRequestScope } from '../lib/routeRequestAbort'
import { beginRouteTransition, finishRouteTransition } from '../lib/routeTransition'
import { lazyRoute } from './lazyRoute'
import { isDynamicImportFailure, reloadOnceForChunkLoadFailure } from './chunkLoadRecovery'
import { LOGIN_ROUTE_NAME, withoutLegacySessionReason } from '../lib/loginNavigation'
import { PROTECTION_RETENTION_LEGACY_ROUTE } from './legacyRoutes'

import { prefetchAuthTurnstile } from '../composables/useTurnstileConfig'

const authTurnstilePrefetch = () => {
  prefetchAuthTurnstile()
}

const fullscreenRouteMeta = { layout: 'fullscreen' } as const

const AppShell = () => import('../app/layout/AppShell.vue')
const PlatformOpsShell = () => import('../platform-ops/layout/PlatformOpsShell.vue')
const LoginPage = lazyRoute(() => import('../pages/auth/Login.vue'))
const RegisterPage = lazyRoute(() => import('../pages/auth/Register.vue'))
const OAuthCallbackPage = lazyRoute(() => import('../pages/auth/OAuthCallback.vue'))
const OAuthErrorPage = lazyRoute(() => import('../pages/auth/OAuthError.vue'))
const DashboardPage = lazyRoute(() => import('../pages/Dashboard.vue'))
const ProtectionDataPage = lazyRoute(() => import('../pages/protection/DataProtection.vue'))
const ProtectionSnapshotRestorePage = lazyRoute(() => import('../pages/protection/SnapshotRestorePage.vue'))
const ProtectionBackupCreateWizardPage = lazyRoute(() => import('../pages/protection/BackupCreateWizard.vue'))
const ProtectionBackupSourcesPage = lazyRoute(() => import('../pages/protection/BackupSources.vue'))
const ProtectionBackupDetailPage = lazyRoute(() => import('../pages/protection/BackupDetail.vue'))
const ProtectionSnapshotDetailPage = lazyRoute(() => import('../pages/protection/SnapshotDetail.vue'))
const ProtectionPoliciesPage = lazyRoute(() => import('../pages/protection/Policies.vue'))
const ProtectionPolicyEditorPage = lazyRoute(() => import('../pages/protection/PolicyEditorPage.vue'))
const ProtectionFileFilterRuleGuidePage = lazyRoute(() => import('../pages/protection/FileFilterRuleGuide.vue'))
const AssetsNodesPage = lazyRoute(() => import('../pages/node/Nodes.vue'))
const AssetsRepositoriesPage = lazyRoute(() => import('../pages/node/Repositories.vue'))
const AddS3RepositoryPage = lazyRoute(() => import('../pages/node/AddS3Repo.vue'))
const EditS3RepositoryPage = lazyRoute(() => import('../pages/node/EditS3Repo.vue'))
const AddNasRepositoryPage = lazyRoute(() => import('../pages/node/AddNasRepository.vue'))
const RepairNasRepositoryPage = lazyRoute(() => import('../pages/node/RepairNasRepository.vue'))
const AddProxyFsRepositoryPage = lazyRoute(() => import('../pages/node/AddProxyFsRepository.vue'))
const EditProxyFsRepositoryPage = lazyRoute(() => import('../pages/node/EditProxyFsRepo.vue'))
const NodesDeployPage = lazyRoute(() => import('../pages/node/NodesDeploy.vue'))
const AssetsSnapshotsPage = lazyRoute(() => import('../pages/node/Snapshots.vue'))
const OpsTasksPage = lazyRoute(() => import('../pages/ops/Tasks.vue'))
const OpsAttentionPage = lazyRoute(() => import('../pages/ops/Attention.vue'))
const OpsAlertPoliciesPage = lazyRoute(() => import('../pages/ops/AlertPolicies.vue'))
const OpsAlertPolicyEditorPage = lazyRoute(() => import('../pages/ops/AlertPolicyEditorPage.vue'))
const OpsAlertIncidentsPage = lazyRoute(() => import('../pages/ops/AlertIncidents.vue'))
const OpsNotificationChannelsPage = lazyRoute(() => import('../pages/ops/NotificationChannels.vue'))
const OpsNotificationChannelEditorPage = lazyRoute(() => import('../pages/ops/NotificationChannelEditorPage.vue'))
const OpsNotificationRecordsPage = lazyRoute(() => import('../pages/ops/NotificationRecords.vue'))
const OpsAuditPage = lazyRoute(() => import('../pages/ops/Audit.vue'))
const SettingsMembersPage = lazyRoute(() => import('../pages/settings/Members.vue'))
const OrganizationHubPage = lazyRoute(() => import('../pages/settings/OrganizationHub.vue'))
const SubscriptionPage = lazyRoute(() => import('../pages/settings/Subscription.vue'))
const AccountSettingsLayout = lazyRoute(() => import('../pages/account/AccountSettingsLayout.vue'))
const AccountProfilePage = lazyRoute(() => import('../pages/account/AccountProfile.vue'))
const InsightPage = lazyRoute(() => import('../pages/insight/Insight.vue'))
const GlobalSearchPage = lazyRoute(() => import('../pages/Search.vue'))
const NotificationsPage = lazyRoute(() => import('../pages/Notifications.vue'))

export const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: LOGIN_ROUTE_NAME, component: LoginPage, beforeEnter: authTurnstilePrefetch },
    { path: '/register', component: RegisterPage, beforeEnter: authTurnstilePrefetch },
    { path: '/auth/oauth/callback', component: OAuthCallbackPage },
    { path: '/auth/oauth/error', component: OAuthErrorPage },
    {
      path: '/',
      component: AppShell,
      children: [
        { path: '', component: DashboardPage },
        { path: 'protection', redirect: '/protection/backups' },
        { path: 'protection/backups', component: ProtectionDataPage },
        { path: 'protection/backups/create', component: ProtectionBackupCreateWizardPage, meta: fullscreenRouteMeta },
        {
          path: 'protection/restore/snapshots/:snapshotId',
          name: 'protection-snapshot-restore',
          component: ProtectionSnapshotRestorePage,
          meta: fullscreenRouteMeta,
        },
        { path: 'protection/backup-sources', component: ProtectionBackupSourcesPage },
        {
          path: 'protection/backups/:backupId',
          component: ProtectionBackupDetailPage,
        },
        {
          path: 'protection/backups/:backupId/snapshots/:snapshotId',
          component: ProtectionSnapshotDetailPage,
        },
        { path: 'protection/policies', component: ProtectionPoliciesPage },
        { path: 'protection/policies/create', component: ProtectionPolicyEditorPage, meta: fullscreenRouteMeta },
        { path: 'protection/policies/:id/edit', component: ProtectionPolicyEditorPage, meta: fullscreenRouteMeta },
        { path: 'protection/file-filter-rules/help', component: ProtectionFileFilterRuleGuidePage },
        PROTECTION_RETENTION_LEGACY_ROUTE,
        { path: 'insight', redirect: '/insight/copilot' },
        { path: 'insight/copilot', component: InsightPage },
        {
          path: 'insight/copilot/shared',
          component: lazyRoute(() => import('../pages/insight/SharedCopilotQA.vue')),
          meta: fullscreenRouteMeta,
        },
        {
          path: 'insight/copilot/new',
          component: lazyRoute(() => import('../pages/insight/NewCopilotChat.vue')),
          meta: fullscreenRouteMeta,
        },
        {
          path: 'insight/usage',
          component: lazyRoute(() => import('../pages/insight/InsightUsage.vue')),
        },
        {
          path: 'insight/gateways',
          component: lazyRoute(() => import('../pages/insight/InsightGateways.vue')),
        },
        { path: 'insight/:section', redirect: '/insight/copilot' },
        { path: 'node', redirect: '/node/organization' },
        { path: 'node/agents', component: AssetsNodesPage },
        { path: 'node/gateways', redirect: '/insight/gateways' },
        { path: 'node/nodes/deploy', component: NodesDeployPage, meta: fullscreenRouteMeta },
        { path: 'node/repositories', component: AssetsRepositoriesPage },
        { path: 'node/repositories/s3/add', component: AddS3RepositoryPage, meta: fullscreenRouteMeta },
        { path: 'node/repositories/s3/:id/edit', component: EditS3RepositoryPage, meta: fullscreenRouteMeta },
        { path: 'node/repositories/nas/add', component: AddNasRepositoryPage, meta: fullscreenRouteMeta },
        { path: 'node/repositories/nas/:id/repair', component: RepairNasRepositoryPage, meta: fullscreenRouteMeta },
        { path: 'node/repositories/proxy-fs/add', component: AddProxyFsRepositoryPage, meta: fullscreenRouteMeta },
        { path: 'node/repositories/proxy-fs/:id/edit', component: EditProxyFsRepositoryPage, meta: fullscreenRouteMeta },
        { path: 'node/knowledge-base', redirect: '/platform-ops/engine/knowledge-base' },
        { path: 'node/knowledge-base/add', redirect: '/platform-ops/engine/knowledge-base/add' },
        { path: 'node/knowledge-base/:id/edit', redirect: (to) => `/platform-ops/engine/knowledge-base/${to.params.id}/edit` },
        { path: 'node/assistants', redirect: '/platform-ops/engine/assistants' },
        { path: 'node/assistants/add', redirect: '/platform-ops/engine/assistants/add' },
        { path: 'node/assistants/:uuid/edit', redirect: (to) => `/platform-ops/engine/assistants/${to.params.uuid}/edit` },
        { path: 'node/skills', redirect: '/platform-ops/engine/skills' },
        { path: 'node/skills/add', redirect: '/platform-ops/engine/skills/add' },
        { path: 'node/skills/:uuid/edit', redirect: (to) => `/platform-ops/engine/skills/${to.params.uuid}/edit` },
        { path: 'node/mcp-servers', redirect: '/platform-ops/engine/mcp-servers' },
        { path: 'node/mcp-servers/add', redirect: '/platform-ops/engine/mcp-servers/add' },
        { path: 'node/mcp-servers/:uuid/edit', redirect: (to) => `/platform-ops/engine/mcp-servers/${to.params.uuid}/edit` },
        { path: 'node/ai-settings', redirect: '/platform-ops/engine/ai-settings' },
        { path: 'node/ai-settings/add', redirect: '/platform-ops/engine/ai-settings/add' },
        { path: 'node/ai-settings/:uuid/edit', redirect: (to) => `/platform-ops/engine/ai-settings/${to.params.uuid}/edit` },
        { path: 'node/organization', component: OrganizationHubPage },
        { path: 'node/members', component: SettingsMembersPage },
        { path: 'node/subscription', component: SubscriptionPage },
        { path: 'node/snapshots', component: AssetsSnapshotsPage },
        { path: 'ops', redirect: '/ops/health' },
        ...tenantOpsRoutes,
        { path: 'ops/health', component: OpsAttentionPage },
        { path: 'ops/tasks', component: OpsTasksPage },
        { path: 'ops/alerts', component: OpsAlertIncidentsPage },
        { path: 'ops/alerts/rules', component: OpsAlertPoliciesPage },
        { path: 'ops/alerts/rules/create', component: OpsAlertPolicyEditorPage, meta: fullscreenRouteMeta },
        { path: 'ops/alerts/rules/:id/edit', component: OpsAlertPolicyEditorPage, meta: fullscreenRouteMeta },
        { path: 'ops/channels/create', component: OpsNotificationChannelEditorPage, meta: fullscreenRouteMeta },
        { path: 'ops/channels/:id/edit', component: OpsNotificationChannelEditorPage, meta: fullscreenRouteMeta },
        { path: 'ops/channels', component: OpsNotificationChannelsPage },
        { path: 'ops/delivery-history', component: OpsNotificationRecordsPage },
        { path: 'ops/audit-logs', component: OpsAuditPage },
        {
          path: 'account',
          component: AccountSettingsLayout,
          redirect: '/account/profile',
          children: [
            { path: 'profile', component: AccountProfilePage },
            { path: 'notifications', component: NotificationsPage },
          ],
        },
        { path: 'search', component: GlobalSearchPage },
      ],
    },
    {
      path: '/platform-ops',
      component: PlatformOpsShell,
      meta: { requiresPlatformOps: true },
      children: platformOpsRoutes,
    },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

router.beforeEach((to) => {
  if (to.name !== LOGIN_ROUTE_NAME) return
  const query = withoutLegacySessionReason(to.query)
  if (!query) return
  return {
    path: to.path,
    query,
    hash: to.hash,
    replace: true,
  }
})

router.beforeEach((to, from) => {
  if (to.fullPath !== from.fullPath) {
    beginRouteTransition()
    beginRouteRequestScope()
  }
})

router.afterEach(() => {
  void nextTick().then(() => finishRouteTransition())
})

router.onError((error, to) => {
  finishRouteTransition()
  if (isDynamicImportFailure(error)) {
    reloadOnceForChunkLoadFailure(to.fullPath)
  }
})

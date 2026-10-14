import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Route, CalendarDays, HardDrive, Cloud, Filter, Tags } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { sourceAgentSidebarIcon, targetNasSidebarIcon } from '../lib/resourceIcons'
import { useTenantPermissions } from './useTenantPermissions'
import { sourceHostIcon, sourceNasIcon } from '../lib/sourceTypeIcons'

export function useProtectionSideNav() {
  const { t } = useI18n()
  const { has } = useTenantPermissions()
  const required: Record<string, string> = {
    '/protection/backups': 'protection.backup_configurations.view',
    '/protection/backup-sources?tab=host': 'protection.source_hosts.view',
    '/protection/backup-sources?tab=nas': 'protection.source_nas.view',
    '/node/agents': 'protection.proxy_hosts.view',
    '/protection/source-tags': 'protection.tags.view',
    '/node/repositories?tab=s3': 'protection.object_storage_repositories.view',
    '/node/repositories?tab=nas': 'protection.target_nas_repositories.view',
    '/node/repositories?tab=proxy_fs': 'protection.local_disk_repositories.view',
    '/protection/policies?tab=backup': 'protection.backup_policies.view',
    '/protection/policies?tab=filter': 'protection.file_filters.view',
  }
  return computed<MenuItem[]>(() => ([
    {
      label: t('protection.side.groupDataSecurity'),
      children: [
        { label: t('protection.side.dataProtection'), to: '/protection/backups', icon: Route },
      ],
    },
    {
      label: t('protection.side.groupResourceManagement'),
      children: [
        {
          label: t('protection.side.sourceHosts'),
          to: '/protection/backup-sources?tab=host',
          pageTitle: t('protection.side.sourceHosts'),
          icon: sourceHostIcon,
        },
        {
          label: t('protection.side.sourceNas'),
          to: '/protection/backup-sources?tab=nas',
          pageTitle: t('protection.side.sourceNas'),
          icon: sourceNasIcon,
        },
        {
          label: t('protection.side.sourceAgents'),
          to: '/node/agents',
          pageTitle: t('protection.side.sourceAgents'),
          icon: sourceAgentSidebarIcon,
        },
        { label: t('protection.side.sourceTags'), to: '/protection/source-tags', icon: Tags },
      ],
    },
    {
      label: t('protection.side.groupDrTargets'),
      children: [
        { label: t('protection.side.objectStorage'), to: '/node/repositories?tab=s3', icon: Cloud, pageTitle: t('protection.side.objectStorage') },
        { label: t('repositoriesPage.tabNas'), to: '/node/repositories?tab=nas', icon: targetNasSidebarIcon, pageTitle: t('repositoriesPage.tabNas') },
        { label: t('protection.side.standaloneDisks'), to: '/node/repositories?tab=proxy_fs', icon: HardDrive, pageTitle: t('protection.side.standaloneDisks') },
      ],
    },
    {
      label: t('protection.side.groupPolicyManagement'),
      children: [
        {
          label: t('protection.side.backupPolicies'),
          to: '/protection/policies?tab=backup',
          pageTitle: t('protection.side.backupPolicies'),
          icon: CalendarDays,
        },
        {
          label: t('protection.side.fileFilterRules'),
          to: '/protection/policies?tab=filter',
          pageTitle: t('protection.side.fileFilterRules'),
          icon: Filter,
        },
      ],
    },
  ] as MenuItem[]).map((section) => ({
    ...section, children: section.children?.filter((item) => !required[item.to || ''] || has(required[item.to || '']!)),
  })).filter((section) => section.children?.length))
}

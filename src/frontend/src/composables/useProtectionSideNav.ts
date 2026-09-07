import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Route, CalendarDays, HardDrive, Cloud, Filter } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'
import { sourceAgentSidebarIcon, targetNasSidebarIcon } from '../lib/resourceIcons'
import { sourceHostIcon, sourceNasIcon } from '../lib/sourceTypeIcons'

export function useProtectionSideNav() {
  const { t } = useI18n()
  return computed<MenuItem[]>(() => [
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
  ])
}

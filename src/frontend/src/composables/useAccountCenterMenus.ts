import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bell, Gauge, User } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'

/** Personal Account section shared by Configuration and account pages. */
export function useAccountCenterMenus() {
  const { t } = useI18n()
  return computed<MenuItem[]>(() => [
    {
      label: t('account.groupAccountCenter'),
      children: [
        {
          to: '/account/profile',
          label: t('account.sidebarProfile'),
          pageTitle: t('account.pageProfileTitle'),
          icon: User,
        },
        ...(__HFL_EXTENSIONS_FRONTEND__ ? [{
          to: '/account/usage',
          label: t('accountUsage.title'),
          pageTitle: t('accountUsage.title'),
          icon: Gauge,
        }] : []),
        {
          to: '/account/notifications',
          label: t('account.sidebarNotifications'),
          pageTitle: t('account.pageNotificationsTitle'),
          icon: Bell,
        },
      ],
    },
  ])
}

/** @deprecated Use useAccountCenterMenus */
export const useAdminConsoleMenus = useAccountCenterMenus

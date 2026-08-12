import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { Bell, User } from 'lucide-vue-next'
import type { MenuItem } from '../components/ModulePage.vue'

/** Account center sidebar menus. */
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

import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Cpu,
  Settings,
} from 'lucide-vue-next'
import type { MenuItem } from '../../components/ModulePage.vue'
import { fetchDeployProfile } from '../../composables/useDeployProfile'

/**
 * Community side nav: AI Models + optional Runtime only.
 * Email / Authentication / Data Gateways stay out of the community shell;
 * those pages remain routable so the platform extension can merge them.
 */
export function usePlatformOpsSideNav() {
  const { t } = useI18n()

  return computed<MenuItem[]>(() => [
    {
      label: t('platformOps.nav.groupEngine'),
      children: [
        {
          label: t('platformOps.nav.engineModels'),
          to: '/platform-ops/engine/ai-settings',
          icon: Cpu,
        },
      ],
    },
    {
      label: t('platformOps.nav.groupPlatform'),
      children: [
        {
          label: t('platformOps.nav.platformRuntime'),
          to: '/platform-ops/platform/runtime-environment',
          icon: Settings,
          pageTitle: t('platformOps.settings.environmentTitle'),
        },
      ],
    },
  ])
}

export function usePlatformOpsAccess() {
  const ready = ref(false)
  const emailSignupEnabled = ref(false)
  const tenantPublicUrl = ref('')

  async function load() {
    const profile = await fetchDeployProfile()
    emailSignupEnabled.value = !!profile?.email_signup_enabled
    tenantPublicUrl.value = profile?.tenant_public_url || ''
    ready.value = true
    return profile
  }

  onMounted(() => {
    void load()
  })

  return {
    ready,
    emailSignupEnabled,
    tenantPublicUrl,
    load,
  }
}

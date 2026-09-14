import { computed, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  Container,
  Cpu,
  Network,
} from 'lucide-vue-next'
import type { MenuItem } from '../../components/ModulePage.vue'
import { fetchDeployProfile } from '../../composables/useDeployProfile'

/**
 * Community side nav: AI Models and essential instance administration.
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
          label: t('platformOps.nav.platformExternalAccess'),
          to: '/platform-ops/platform/external-access',
          icon: Network,
          pageTitle: t('platformOps.settings.externalAccessTitle'),
        },
        {
          label: t('platformOps.nav.platformRuntime'),
          to: '/platform-ops/platform/runtime-environment',
          icon: Container,
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

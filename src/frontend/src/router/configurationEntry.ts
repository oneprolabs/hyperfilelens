import { ElMessage } from 'element-plus'
import { useTenantPermissions } from '../composables/useTenantPermissions'
import { i18n } from '../i18n'
import { configurationEntryTarget, resolveConfigurationEntry, type ConfigurationRoute } from '../lib/configurationAccess'
export { resolveConfigurationEntry } from '../lib/configurationAccess'

/** Called only after authentication, via the /node route's beforeEnter. */
export function enterConfiguration(
  routes: readonly ConfigurationRoute[], location?: { query?: Record<string, unknown> },
) {
  const { has } = useTenantPermissions()
  const path = resolveConfigurationEntry(routes, has)
  if (path === '/') {
    ElMessage.warning(i18n.global.t('settings.nav.accessDenied'))
  }
  const target = configurationEntryTarget(path, location)
  const [targetPath, search] = target.split('?')
  const query: Record<string, string> = {}
  new URLSearchParams(search).forEach((value, key) => { query[key] = value })
  return search ? { path: targetPath, query, replace: true } : { path, replace: true }
}

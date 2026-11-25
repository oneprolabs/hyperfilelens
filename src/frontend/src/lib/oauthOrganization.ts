import { currentUser, fetchCurrentUser, setStoredOrgKey, type User } from '../composables/useAuth'
import { api } from './api'
import { asList, unwrapApiPayload } from './parse'
import { chooseLoginOrganization } from './organizationStorage'
import { readLoginOrganizationPreference, rememberLoginOrganization } from './loginOrganizationPreference'

/** Resolve the authenticated account before consulting any browser login hint. */
export async function restoreOAuthOrganization(): Promise<User> {
  // Neither a previous browser user's context nor a callback query is authority.
  setStoredOrgKey('')
  let user = await fetchCurrentUser({ rememberOrganization: false })
  if (!user) throw new Error('OAuth session is unavailable.')
  const userId = user.id
  const fallback = user.access_profile?.org_key || ''
  const remembered = readLoginOrganizationPreference(userId)
  if (!user.requires_password_change && remembered && remembered !== fallback) {
    const choices = asList<{ key: string }>(await api('/api/v1/iam/my-organizations/'))
    if (currentUser.value?.id !== userId) throw new Error('OAuth account changed.')
    const target = chooseLoginOrganization(
      choices.map(row => ({ org_key: row.key })), remembered, fallback,
    )
    if (target && target !== fallback) {
      const result = unwrapApiPayload<{ access_profile?: { org_key: string } }>(await api(
        '/api/v1/iam/switch-organization/', {
          method: 'POST', body: JSON.stringify({ org_key: target }),
        },
      ))
      if (currentUser.value?.id !== userId || result.access_profile?.org_key !== target) {
        throw new Error('OAuth organization confirmation changed.')
      }
      setStoredOrgKey(target)
      user = await fetchCurrentUser({ rememberOrganization: false })
      if (user?.id !== userId || user.access_profile?.org_key !== target) {
        throw new Error('OAuth organization is no longer available.')
      }
    }
  }
  if (currentUser.value?.id !== userId) throw new Error('OAuth account changed.')
  const orgKey = user.access_profile?.org_key
  if (orgKey && !user.requires_password_change) rememberLoginOrganization(userId, orgKey)
  return user
}

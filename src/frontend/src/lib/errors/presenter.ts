import { currentUser } from '../../composables/useAuth'
import { notifyError } from '../notify'
import { isAbortError } from './normalizer'
import {
  resolveErrorMessage,
  resolveQuotaIncreaseRequestPath,
  type TranslateFn,
} from './resolver'
import type { ErrorContext } from './types'

function canManageUsageRequests(): boolean {
  if (!__HFL_EXTENSIONS_FRONTEND__) return false
  const grants = currentUser.value?.access_profile?.permissions || []
  return grants.includes('configuration.subscriptions.manage')
}

export function presentError(
  err: unknown,
  ctx: ErrorContext,
  t?: TranslateFn,
  fallback = 'Request failed',
): string {
  if (isAbortError(err)) return ''
  if (ctx.kind === 'background') return ''

  const message = resolveErrorMessage(err, t, fallback)
  if (!message) return ''

  if (ctx.kind === 'mutation') {
    const requestPath = __HFL_EXTENSIONS_FRONTEND__ && canManageUsageRequests()
      ? resolveQuotaIncreaseRequestPath(err)
      : null
    if (requestPath) {
      notifyError({
        message,
        action: {
          label: t
            ? t('errors.codes.subscriptionQuotaRequestAction')
            : 'Request Quota Increase',
          onClick: async () => {
            const { router } = await import('../../router')
            await router.push({
              path: requestPath.path,
              query: requestPath.query,
            })
          },
        },
      })
    } else {
      notifyError(message)
    }
    return message
  }

  // page-load / drawer-load / form / offline: caller renders inline UI
  return message
}

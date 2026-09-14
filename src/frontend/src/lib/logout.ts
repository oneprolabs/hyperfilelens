import type { Router } from 'vue-router'
import { ElMessageBox } from 'element-plus'
import { clearAuth } from '../composables/useAuth'
import { getCorrelationHeaders } from './requestContext'
import { logger } from './logger'

let signOutConfirmPending = false

/** Show a single logout confirmation dialog (deduped). Returns true when confirmed. */
export async function confirmSignOut(t: (key: string) => string): Promise<boolean> {
  if (signOutConfirmPending) return false
  signOutConfirmPending = true
  try {
    ElMessageBox.close()
    await ElMessageBox.confirm(t('account.logoutConfirmBody'), t('account.logoutConfirmTitle'), {
      confirmButtonText: t('common.confirm'),
      cancelButtonText: t('common.cancel'),
      type: 'warning',
      customClass: 'hfl-message-box--sign-out',
      appendTo: document.body,
      closeOnClickModal: false,
      // Keep the confirm action in its neutral state when the dialog opens.
      // Auto-focusing it also makes an immediate Enter keypress sign the user
      // out without an explicit confirmation.
      autofocus: false,
    })
    return true
  } catch {
    return false
  } finally {
    signOutConfirmPending = false
  }
}

export async function performLogout(router: Router): Promise<void> {
  try {
    await fetch('/api/v1/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: getCorrelationHeaders({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({}),
    })
  } catch (e) {
    logger.warn('logout.ts', 41, 'API call failed, proceeding anyway', e)
  }
  // Clear in-memory auth state before navigation
  clearAuth()
  try {
    await router.replace('/login')
  } catch (e) {
    logger.error('logout.ts', 54, 'Navigation failed', e)
    // Fallback: use window.location
    window.location.href = '/login'
  }
}

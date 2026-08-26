import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  DEFAULT_LOCALE,
  getAvailableLocaleCodes,
  selectLocale as applyLocale,
  setAuthenticatedLocalePreference,
} from '../i18n'
import { hasMultipleLocales, installedLangPacks } from '../lib/langPacks'
import { api } from '../lib/api'
import { notifyError } from '../lib/notify'
import { currentUser } from './useAuth'

let localePreferenceWriteQueue: Promise<void> = Promise.resolve()

export function useLocaleSwitch() {
  const { locale, t } = useI18n()
  const canSwitchLocale = computed(() => hasMultipleLocales())
  const localeLabel = (code: string) => {
    if (code === DEFAULT_LOCALE) return 'English'
    return (
      installedLangPacks.value.find((pack) => pack.frontend_code === code)?.display_name ?? code
    )
  }
  const currentLocaleLabel = computed(() => localeLabel(String(locale.value)))
  const localeOptions = computed(() =>
    getAvailableLocaleCodes().map((code) => ({
      code,
      label: localeLabel(code),
    })),
  )

  function applySelectedLocale(code: string) {
    const selected = applyLocale(code)
    const user = currentUser.value
    if (!user) return true
    const profileLanguage = selected === DEFAULT_LOCALE
      ? DEFAULT_LOCALE
      : installedLangPacks.value.find((pack) => pack.frontend_code === selected)?.backend_code
    if (!profileLanguage) return true
    if (user.language === profileLanguage) {
      setAuthenticatedLocalePreference(profileLanguage)
      return true
    }
    currentUser.value = { ...user, language: profileLanguage }
    setAuthenticatedLocalePreference(profileLanguage)
    localePreferenceWriteQueue = localePreferenceWriteQueue.catch(() => undefined).then(async () => {
      const activeUser = currentUser.value
      if (activeUser?.id !== user.id || activeUser.language !== profileLanguage) return
      try {
        await api('/api/v1/auth/user', {
          method: 'PATCH',
          body: JSON.stringify({ language: profileLanguage }),
        })
      } catch (error) {
        const activeUser = currentUser.value
        if (activeUser?.id !== user.id || activeUser.language !== profileLanguage) return
        notifyError({
          message: t('errors.generic.requestFailed'),
          error,
          dedupeKey: 'language-preference-save-failed',
        })
      }
    })
    return true
  }

  function selectLocale(code: string) {
    if (!canSwitchLocale.value || !getAvailableLocaleCodes().includes(code)) return false
    if (String(locale.value) === code) return false
    return applySelectedLocale(code)
  }

  function syncAuthenticatedLocale(code: string) {
    if (!canSwitchLocale.value || !getAvailableLocaleCodes().includes(code)) return false
    return applySelectedLocale(code)
  }

  return {
    canSwitchLocale,
    currentLocaleLabel,
    localeOptions,
    selectLocale,
    syncAuthenticatedLocale,
    locale,
  }
}

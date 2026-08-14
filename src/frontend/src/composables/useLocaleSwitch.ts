import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import {
  DEFAULT_LOCALE,
  getAvailableLocaleCodes,
  selectLocale,
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
  const nextLocaleCode = computed(() => {
    const available = getAvailableLocaleCodes()
    const currentIndex = available.indexOf(String(locale.value))
    return available[(currentIndex + 1) % available.length] ?? DEFAULT_LOCALE
  })
  const nextLocaleLabel = computed(() => localeLabel(nextLocaleCode.value))

  function toggleLocale() {
    if (!canSwitchLocale.value) return
    const selected = selectLocale(nextLocaleCode.value)
    const user = currentUser.value
    if (!user) return
    const profileLanguage = selected === DEFAULT_LOCALE
      ? DEFAULT_LOCALE
      : installedLangPacks.value.find((pack) => pack.frontend_code === selected)?.backend_code
    if (!profileLanguage) return
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
  }

  return {
    canSwitchLocale,
    currentLocaleLabel,
    nextLocaleCode,
    nextLocaleLabel,
    toggleLocale,
    locale,
  }
}

/** Map vue-i18n locale to a Cloudflare Turnstile language code. */
export function turnstileLanguageFromAppLocale(appLocale: string): string {
  const locale = appLocale.trim().toLowerCase().replaceAll('_', '-')

  // The application uses zh-hans for Simplified Chinese, while Turnstile
  // expects the regional code zh-cn for its Simplified Chinese UI.
  if (locale === 'zh' || locale === 'zh-hans' || locale === 'zh-cn') {
    return 'zh-cn'
  }

  return locale || 'en'
}

import { computed, type ComputedRef } from 'vue'
import { useData } from 'vitepress'
import { isHomeLocale, type HomeLocale } from './homeCopy'

/** VitePress names the default locale "root"; the copy tables key it as "en". */
export function useSiteLocale(): ComputedRef<HomeLocale> {
  const { localeIndex } = useData()
  return computed(() => {
    const index = localeIndex.value === 'root' ? 'en' : localeIndex.value
    return isHomeLocale(index) ? index : 'en'
  })
}

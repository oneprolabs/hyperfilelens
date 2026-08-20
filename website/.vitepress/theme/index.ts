import type { Theme } from 'vitepress'
import DefaultTheme from 'vitepress/theme'
import Layout from './Layout.vue'
import './custom.css'
import './docs.css'
import { initWebsiteAnalytics, trackWebsitePageView } from './analytics'

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ router }) {
    if (typeof window === 'undefined') return
    initWebsiteAnalytics()
    router.onAfterRouteChange = (to) => {
      trackWebsitePageView(to)
    }
  },
} satisfies Theme

import { createApp, watchEffect } from 'vue'
import './index.css'
import 'element-plus/dist/index.css'
import './styles/element-plus-tag.css'
import './styles/element-plus-message.css'
import './styles/element-plus-message-box.css'
import './styles/directory-tree.css'
import './styles/element-plus-table.css'
import './styles/element-plus-date-picker.css'
import './styles/auth-autofill.css'
import './styles/list-page-ui.css'
import './styles/source-list-ui.css'
import './styles/repository-list-ui.css'
import './styles/ops-list-ui.css'
import './styles/detail-page-ui.css'
import './styles/nav-dropdown-panel.css'
import './styles/element-plus-popper.css'
import App from './App.vue'
import { router } from './router'
import { i18n, resolveLocaleAfterPacksLoaded } from './i18n'
import { loadInstalledLangPacks } from './lib/langPacks'
import { setupElementPlus } from './plugins/element-plus'
import { setLocale } from './lib/api'
import { setupAuthGuard, setupSessionWatchdog } from './composables/useAuth'
import { initSentry } from './lib/sentry'
import { initAppAnalytics } from './lib/analytics'

async function bootstrap() {
  const app = createApp(App)
  await initSentry(app, router)
  initAppAnalytics(router)
  app.use(i18n)
  app.use(router)
  setupElementPlus(app)

  watchEffect(() => {
    setLocale(String(i18n.global.locale.value))
  })

  setupAuthGuard()
  setupSessionWatchdog()
  app.mount('#app')

  void loadInstalledLangPacks().then(() => {
    resolveLocaleAfterPacksLoaded()
  })
}

void bootstrap()

import type { Theme } from 'vitepress'
import DefaultTheme from 'vitepress/theme'
import { nextTick } from 'vue'
import Layout from './Layout.vue'
import './custom.css'
import './docs.css'
import { initWebsiteAnalytics, trackWebsitePageView } from './analytics'
import { enDocA11yLabels, zhDocA11yLabels } from './languages'

function localizeDocCopyButtons(path: string) {
  const labels = path.startsWith('/zh/docs')
    ? zhDocA11yLabels
    : path.startsWith('/en/docs')
      ? enDocA11yLabels
      : null
  if (!labels) return

  document.querySelectorAll<HTMLButtonElement>('.vp-doc button.copy').forEach((button) => {
    const updateLabel = () => {
      const label = button.classList.contains('copied') ? labels.codeCopied : labels.copyCode
      button.setAttribute('aria-label', label)
      button.setAttribute('title', label)
    }

    updateLabel()
    if (button.dataset.hflCopyEnhanced) return
    button.dataset.hflCopyEnhanced = 'true'
    new MutationObserver(updateLabel).observe(button, {
      attributes: true,
      attributeFilter: ['class'],
    })
  })
}

function localizeDocLabels(path: string) {
  const labels = path.startsWith('/zh/docs')
    ? zhDocA11yLabels
    : path.startsWith('/en/docs')
      ? enDocA11yLabels
      : null
  if (!labels) return

  document
    .querySelector('.VPNavBarHamburger')
    ?.setAttribute('aria-label', labels.mobileNavigation)

  const searchButton = document.querySelector('.VPNavBarSearch .DocSearch-Button')
  if (searchButton) {
    if (window.matchMedia('(max-width: 767px)').matches) {
      searchButton.setAttribute('aria-label', labels.search)
    } else {
      // On desktop the visible label already provides the accessible name.
      searchButton.removeAttribute('aria-label')
    }
  }

  const sidebarLabel = document.querySelector('#sidebar-aria-label')
  if (sidebarLabel) sidebarLabel.textContent = labels.sidebar

  const footerLabel = document.querySelector('#doc-footer-aria-label')
  if (footerLabel) footerLabel.textContent = labels.pager

}

const englishDocRoutes = new Set([
  '/docs',
  '/docs/getting-started/saas',
  '/docs/getting-started/install',
  '/docs/product',
  '/docs/backup-restore',
  '/docs/backup-restore/sources',
  '/docs/backup-restore/targets',
  '/docs/backup-restore/create-backup',
  '/docs/backup-restore/policies',
  '/docs/backup-restore/snapshots',
  '/docs/backup-restore/restore',
  '/docs/insights',
  '/docs/deployment',
  '/docs/deployment/requirements',
  '/docs/deployment/network',
  '/docs/deployment/post-install',
  '/docs/deployment/agent',
  '/docs/deployment/proxy',
  '/docs/deployment/data-gateway',
  '/docs/deployment/lifecycle',
  '/docs/deployment/operations',
  '/docs/help',
  '/docs/reference',
  '/docs/reference/support-matrix',
  '/docs/reference/limitations-security',
  '/docs/troubleshooting',
  '/docs/troubleshooting/account-sign-in',
  '/docs/troubleshooting/installation-nodes',
  '/docs/troubleshooting/protection',
  '/docs/troubleshooting/insights',
])

function normalizeDocRoute(path: string) {
  return path.replace(/\/+$/, '') || '/docs'
}

function englishDocFallback(route: string) {
  if (route.startsWith('/docs/product/') ||
      route.startsWith('/docs/backup-restore/') ||
      route.startsWith('/docs/insights/')) {
    return '/docs/product'
  }
  return '/docs'
}

function updateDocLanguageLinks(path: string) {
  const match = path.match(/^\/(en|zh)(\/docs(?:\/.*)?$)/)
  if (!match) return

  const [, currentLocale, rawRoute] = match
  const route = normalizeDocRoute(rawRoute)
  const targetLocale = currentLocale === 'en' ? 'zh' : 'en'
  const targetRoute = targetLocale === 'en' && !englishDocRoutes.has(route)
    ? englishDocFallback(route)
    : route
  const target = `/${targetLocale}${targetRoute}`

  document
    .querySelectorAll<HTMLAnchorElement>('.VPNavBarTranslations a, .VPNavScreenTranslations a')
    .forEach((link) => {
      link.href = target
    })
}

function decorateDocSidebar(path: string) {
  if (!/^\/(?:en|zh)\/docs(?:\/|$)/.test(path)) return

  const iconSets = {
    quickStart: [
      '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m10 8 5 4-5 4Z"/></svg>',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="m8.5 12 2.2 2.2 4.8-5"/></svg>',
    ],
    product: [
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/></svg>',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3 19 6v5c0 4.5-2.9 8.2-7 10-4.1-1.8-7-5.5-7-10V6l7-3Z"/><path d="m8.5 12 2.2 2.2 4.8-5"/></svg>',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 1.7 5.3L19 10l-5.3 1.7L12 17l-1.7-5.3L5 10l5.3-1.7L12 3Z"/><path d="m19 15 .7 2.3L22 18l-2.3.7L19 21l-.7-2.3L16 18l2.3-.7L19 15Z"/></svg>',
    ],
    operations: [
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/><circle cx="12" cy="12" r="4"/></svg>',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="6" rx="1"/><rect x="4" y="14" width="16" height="6" rx="1"/><path d="M8 7h.01M8 17h.01"/></svg>',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="9" width="16" height="7" rx="1"/><path d="M8 9V6M12 9V5M16 9V6M7 19h10M9 13h.01M12 13h.01M15 13h.01"/></svg>',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 12h4l2-6 4 12 2-6h6"/></svg>',
    ],
    help: [
      '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M9.5 9a2.5 2.5 0 1 1 4.2 1.8c-1 .8-1.7 1.2-1.7 2.7M12 17h.01"/></svg>',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h11a3 3 0 0 1 3 3v13H8a3 3 0 0 1-3-3V4Z"/><path d="M8 4v13a3 3 0 0 0 3 3"/></svg>',
      '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.8-3.8a6 6 0 0 1-7.9 7.9l-6.9 6.9a2.1 2.1 0 0 1-3-3l6.9-6.9a6 6 0 0 1 7.9-7.9Z"/></svg>',
    ],
  } as const

  const docsPath = path.replace(/^\/(?:en|zh)/, '')
  const section = docsPath.startsWith('/docs/deployment/')
    ? 'operations'
    : docsPath.startsWith('/docs/product/') ||
        docsPath.startsWith('/docs/backup-restore/') ||
        docsPath.startsWith('/docs/insights/')
      ? 'product'
      : docsPath.startsWith('/docs/help/') ||
          docsPath.startsWith('/docs/reference/') ||
          docsPath.startsWith('/docs/troubleshooting/')
        ? 'help'
        : 'quickStart'
  const icons = iconSets[section]

  const applyIcons = () => {
    document.querySelectorAll<HTMLElement>('.VPSidebarItem.level-0.hfl-sidebar-current').forEach((group) => {
      group.classList.remove('hfl-sidebar-current')
    })

    document.querySelectorAll<HTMLElement>('.VPSidebarItem.level-0 > .item .text').forEach((title, index) => {
      const icon = icons[index]
      const group = title.closest<HTMLElement>('.VPSidebarItem.level-0')
      if (group && section === 'help' && index === 0 && /^\/(?:en|zh)\/docs\/help\/?$/.test(path)) {
        group.classList.add('hfl-sidebar-current')
      }
      if (group && section === 'help' && index === 0 && !group.querySelector(':scope > .items')) {
        const groupItem = group.querySelector<HTMLElement>(':scope > .item')
        groupItem?.removeAttribute('role')
        groupItem?.removeAttribute('tabindex')
      }
      if (!icon || title.querySelector('.hfl-sidebar-icon')) return
      title.insertAdjacentHTML('afterbegin', `<span class="hfl-sidebar-icon">${icon}</span>`)
    })
  }

  // Linked section headings can be mounted one frame after the regular groups.
  // Re-run once after the sidebar has settled so every top-level group gets the same treatment.
  applyIcons()
  window.setTimeout(applyIcons, 80)
}

function revealActiveSidebarItem(path: string) {
  if (!/^\/(?:en|zh)\/docs(?:\/|$)/.test(path)) return

  const sidebar = document.querySelector<HTMLElement>('.VPSidebar')
  const activeItem = sidebar?.querySelector<HTMLElement>('.VPSidebarItem.is-active')
  if (!sidebar || !activeItem) return

  const sidebarRect = sidebar.getBoundingClientRect()
  const activeRect = activeItem.getBoundingClientRect()
  const visibilityMargin = 16
  const isVisible =
    activeRect.top >= sidebarRect.top + visibilityMargin &&
    activeRect.bottom <= sidebarRect.bottom - visibilityMargin

  if (isVisible) return

  const activeTop = activeRect.top - sidebarRect.top + sidebar.scrollTop
  sidebar.scrollTop = activeTop - (sidebar.clientHeight - activeRect.height) / 2
}

function enhanceDocPage(path: string) {
  window.requestAnimationFrame(() => {
    localizeDocCopyButtons(path)
    localizeDocLabels(path)
    decorateDocSidebar(path)
    revealActiveSidebarItem(path)
    updateDocLanguageLinks(path)
    window.setTimeout(() => updateDocLanguageLinks(path), 80)
  })
}

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ router }) {
    if (typeof window === 'undefined') return
    initWebsiteAnalytics()
    router.onAfterRouteChange = async (to) => {
      // VitePress updates document.title from reactive page data. Wait for that
      // update before deriving the title used in the analytics event.
      await nextTick()
      trackWebsitePageView(to)
      enhanceDocPage(to)
    }
    const mobileQuery = window.matchMedia('(max-width: 767px)')
    mobileQuery.addEventListener('change', () => enhanceDocPage(window.location.pathname))
    enhanceDocPage(window.location.pathname)
  },
} satisfies Theme

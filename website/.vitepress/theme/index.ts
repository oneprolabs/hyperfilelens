import type { Theme } from 'vitepress'
import DefaultTheme from 'vitepress/theme'
import Layout from './Layout.vue'
import './custom.css'
import './docs.css'
import { initWebsiteAnalytics, trackWebsitePageView } from './analytics'
import { zhDocA11yLabels } from './languages'

function localizeChineseDocLabels(path: string) {
  if (!path.startsWith('/zh/docs')) return

  document
    .querySelector('.VPNavBarHamburger')
    ?.setAttribute('aria-label', zhDocA11yLabels.mobileNavigation)

  const searchButton = document.querySelector('.VPNavBarSearch .DocSearch-Button')
  if (searchButton) {
    if (window.matchMedia('(max-width: 767px)').matches) {
      searchButton.setAttribute('aria-label', zhDocA11yLabels.search)
    } else {
      // On desktop the visible label already provides the accessible name.
      searchButton.removeAttribute('aria-label')
    }
  }

  const sidebarLabel = document.querySelector('#sidebar-aria-label')
  if (sidebarLabel) sidebarLabel.textContent = zhDocA11yLabels.sidebar

  const footerLabel = document.querySelector('#doc-footer-aria-label')
  if (footerLabel) footerLabel.textContent = zhDocA11yLabels.pager
}

function revealActiveSidebarItem(path: string) {
  if (!path.startsWith('/zh/docs')) return

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
    localizeChineseDocLabels(path)
    revealActiveSidebarItem(path)
  })
}

export default {
  extends: DefaultTheme,
  Layout,
  enhanceApp({ router }) {
    if (typeof window === 'undefined') return
    initWebsiteAnalytics()
    router.onAfterRouteChange = (to) => {
      trackWebsitePageView(to)
      enhanceDocPage(to)
    }
    const mobileQuery = window.matchMedia('(max-width: 767px)')
    mobileQuery.addEventListener('change', () => enhanceDocPage(window.location.pathname))
    enhanceDocPage(window.location.pathname)
  },
} satisfies Theme

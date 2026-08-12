#!/usr/bin/env node
/** Browser smoke coverage for the three development-stack login surfaces. */

import { createRequire } from 'node:module'

const require = createRequire('/smoke/package.json')
const { chromium } = require('playwright')

const smokeHost = process.env.SMOKE_HOST || 'host.docker.internal'
const tenantPort = process.env.HFL_TENANT_PORT || '11443'
const adminPort = process.env.HFL_ADMIN_PORT || '11444'
const loginPort = process.env.HFL_LOGIN_PORT || tenantPort
const sourceLensPort = process.env.SOURCELENS_CONSOLE_PORT || '11445'
const hflEmail = process.env.SEED_ADMIN_EMAIL || 'admin@hyperfilelens.com'
const hflPassword = process.env.SEED_ADMIN_PASSWORD || 'Admin@123'
const sourceLensUser = process.env.SOURCELENS_USER || 'admin'
const sourceLensPassword = process.env.SOURCELENS_PASSWORD || 'adminpassword'
const requireHmr = process.env.SMOKE_REQUIRE_HMR !== '0'
const skipSourceLens = process.env.SMOKE_SKIP_SOURCELENS === '1'

function fail(message) {
  throw new Error(message)
}

async function waitForHflLogin(page, baseUrl, expectedPathPrefix) {
  const unauthorized = []
  const webSockets = []
  page.on('response', response => {
    if (response.status() === 401) unauthorized.push(response.url())
  })
  page.on('websocket', socket => webSockets.push(socket.url()))

  await page.goto(`${baseUrl}/`, { waitUntil: 'networkidle' })
  await page.waitForURL(url => url.pathname.startsWith('/login'), { timeout: 30_000 })
  if (unauthorized.length) {
    fail(`pre-auth 401 response(s) at ${baseUrl}: ${unauthorized.join(', ')}`)
  }

  await page.locator('input[autocomplete="email"]').fill(hflEmail)
  await page.locator('input[autocomplete="current-password"]').fill(hflPassword)
  const submit = page.locator('button.submit-btn')
  await submit.waitFor({ state: 'visible', timeout: 30_000 })
  await submit.click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 60_000 })
  if (!page.url().includes(expectedPathPrefix)) {
    fail(`unexpected post-login URL at ${baseUrl}: ${page.url()}`)
  }
  await page.waitForTimeout(1_000)
  if (requireHmr && !webSockets.some(url => url.includes('/__vite_hmr'))) {
    fail(`dedicated Vite HMR WebSocket was not observed at ${baseUrl}`)
  }
  return page
}

async function waitForPlatformOps(page, baseUrl) {
  const unauthorized = []
  page.on('response', response => {
    if (response.status() === 401) unauthorized.push(response.url())
  })

  await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' })
  try {
    await page.waitForURL(url => url.pathname.startsWith('/platform-ops'), { timeout: 60_000 })
    await page.locator('.platform-ops-shell').waitFor({ state: 'visible', timeout: 60_000 })
  } catch {
    const body = (await page.locator('body').innerText().catch(() => '')).slice(0, 1_500)
    const cookies = await page.context().cookies().then(items => items.map(item => item.name))
    fail(
      `Platform Operations did not become ready at ${page.url()}; `
      + `cookies=${JSON.stringify(cookies)} body=${JSON.stringify(body)}`,
    )
  }
  if (unauthorized.length) {
    fail(`authenticated Platform Operations returned 401 response(s): ${unauthorized.join(', ')}`)
  }
  return page
}

async function assertNoDocumentOverflow(page, label) {
  const result = await page.evaluate(() => {
    const root = document.documentElement
    const overflow = root.scrollWidth - root.clientWidth
    const offenders = [...document.querySelectorAll('body *')]
      .map(element => {
        const rect = element.getBoundingClientRect()
        return { tag: element.tagName, className: String(element.className || ''), left: rect.left, right: rect.right }
      })
      .filter(item => item.left < -1 || item.right > root.clientWidth + 1)
      .slice(0, 8)
    return { clientWidth: root.clientWidth, scrollWidth: root.scrollWidth, overflow, offenders }
  })
  if (result.overflow > 1) {
    fail(`${label} has document-level horizontal overflow: ${JSON.stringify(result)}`)
  }
}

async function assertInsideViewport(page, locator, label) {
  await locator.waitFor({ state: 'visible', timeout: 30_000 })
  const box = await locator.boundingBox()
  const viewport = page.viewportSize()
  if (!box || !viewport) fail(`${label} has no measurable viewport box`)
  if (box.x < -1 || box.y < -1 || box.x + box.width > viewport.width + 1 || box.y + box.height > viewport.height + 1) {
    fail(`${label} is outside the viewport: box=${JSON.stringify(box)} viewport=${JSON.stringify(viewport)}`)
  }
}

async function assertHorizontallyInsideViewport(page, locator, label) {
  await locator.waitFor({ state: 'visible', timeout: 30_000 })
  const box = await locator.boundingBox()
  const viewport = page.viewportSize()
  if (!box || !viewport) fail(`${label} has no measurable viewport box`)
  if (box.x < -1 || box.x + box.width > viewport.width + 1) {
    fail(`${label} is outside the horizontal viewport: box=${JSON.stringify(box)} viewport=${JSON.stringify(viewport)}`)
  }
}

async function waitForResponsiveRoute(page, url, label) {
  const pageErrors = []
  const failedRequests = []
  const onPageError = error => pageErrors.push(String(error?.stack || error))
  const onRequestFailed = request => {
    failedRequests.push(`${request.method()} ${request.url()}: ${request.failure()?.errorText || 'unknown error'}`)
  }
  page.on('pageerror', onPageError)
  page.on('requestfailed', onRequestFailed)
  process.stdout.write(`[browser smoke] checking ${label}: ${url}\n`)
  try {
    await page.goto(url, { waitUntil: 'domcontentloaded' })
    await page.waitForTimeout(350)
    await page.locator(
      '.dashboard-page, .main-content, .platform-ops-main, .login-form-box, .register-form-box',
    ).first()
      .waitFor({ state: 'visible', timeout: 30_000 })
  } catch (error) {
    const body = (await page.locator('body').innerText().catch(() => '')).slice(0, 1_500)
    fail(
      `${label} did not become ready at ${page.url()}: ${error?.message || error}; `
      + `pageErrors=${JSON.stringify(pageErrors.slice(-5))} `
      + `failedRequests=${JSON.stringify(failedRequests.slice(-10))} body=${JSON.stringify(body)}`,
    )
  } finally {
    page.off('pageerror', onPageError)
    page.off('requestfailed', onRequestFailed)
  }
  await assertNoDocumentOverflow(page, label)

  const table = page.locator('.el-table:visible').first()
  if (await table.count()) {
    await assertHorizontallyInsideViewport(page, table, `${label} table`)
  }
}

async function verifyMobileTenantNavigation(page, baseUrl) {
  await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' })
  const menuButton = page.locator('.mobile-menu-button')
  await menuButton.click()
  const drawer = page.locator('.hfl-mobile-navigation')
  await drawer.waitFor({ state: 'visible' })
  await drawer.locator('a[href="/protection"]').first().click()
  await page.waitForURL(url => url.pathname.startsWith('/protection'))
  await drawer.waitFor({ state: 'hidden' })
}

async function verifyMobilePlatformNavigation(page, baseUrl) {
  await page.goto(`${baseUrl}/platform-ops/monitoring/host`, { waitUntil: 'domcontentloaded' })
  const menuButton = page.locator('.platform-ops-header__menu')
  await menuButton.click()
  const drawer = page.locator('.hfl-mobile-navigation')
  await drawer.waitFor({ state: 'visible' })
  const organizations = drawer.locator('a[href="/platform-ops/orgs"]').first()
  await organizations.click()
  await page.waitForURL(url => url.pathname.startsWith('/platform-ops/orgs'))
  await drawer.waitFor({ state: 'hidden' })
}

async function verifyResponsivePlatformPrimaryAction(page, adminBaseUrl) {
  await page.goto(`${adminBaseUrl}/platform-ops/orgs`, { waitUntil: 'domcontentloaded' })
  const createUser = page.locator('.platform-account-page__lead .el-button').first()
  await createUser.waitFor({ state: 'visible', timeout: 30_000 })
  await createUser.click()
  // UserList consumes ?create=1 immediately and replaces the URL after opening
  // the drawer, so the stable UI state is the route plus the visible drawer.
  await page.waitForURL(url => url.pathname === '/platform-ops/users')
  const drawer = page.locator('.el-drawer:visible').first()
  await drawer.waitFor({ state: 'visible', timeout: 30_000 })
  await page.waitForFunction(() => {
    const element = document.querySelector('.el-drawer')
    if (!element) return false
    const box = element.getBoundingClientRect()
    return box.left >= -1 && box.top >= -1
      && box.right <= window.innerWidth + 1 && box.bottom <= window.innerHeight + 1
  }, null, { timeout: 30_000 })
  await assertInsideViewport(page, drawer, 'Create User drawer')
  await assertNoDocumentOverflow(page, 'Create User drawer')
}

async function verifyAuthenticationViewport(browser, baseUrl, viewport) {
  const context = await browser.newContext({
    ignoreHTTPSErrors: true,
    viewport,
    screen: viewport,
    hasTouch: true,
    isMobile: true,
  })
  try {
    const page = await context.newPage()
    for (const path of ['/login', '/register']) {
      await page.goto(`${baseUrl}${path}`, { waitUntil: 'domcontentloaded' })
      // Self-service signup is disabled in release and SaaS deployments, so
      // /register legitimately redirects to the login form. When signup is
      // enabled, keep validating the dedicated registration form.
      const form = page.locator(
        path === '/login' ? '.login-form-box' : '.register-form-box, .login-form-box',
      ).first()
      await form.waitFor({ state: 'visible' })
      await assertNoDocumentOverflow(page, `${path} at ${viewport.width}x${viewport.height}`)
      await assertInsideViewport(page, form, `${path} form at ${viewport.width}x${viewport.height}`)
    }
  } finally {
    await context.close()
  }
}

async function verifyResponsiveConsoles(browser, storageState, tenantBaseUrl, adminBaseUrl) {
  const profiles = [
    { name: 'mobile-375', viewport: { width: 375, height: 812 }, mobile: true },
    { name: 'mobile-390', viewport: { width: 390, height: 844 }, mobile: true },
    { name: 'tablet-768', viewport: { width: 768, height: 1024 }, mobile: true },
    { name: 'desktop-1280', viewport: { width: 1280, height: 800 }, mobile: false },
  ]
  const tenantRoutes = [
    '/',
    '/protection/backup-sources',
    '/protection/backups',
    '/node/repositories',
    '/ops/tasks',
    '/insight/copilot',
  ]
  const platformRoutes = ['/platform-ops/monitoring/host', '/platform-ops/orgs']

  await verifyAuthenticationViewport(browser, tenantBaseUrl, profiles[0].viewport)
  await verifyAuthenticationViewport(browser, tenantBaseUrl, profiles[2].viewport)
  await verifyAuthenticationViewport(browser, tenantBaseUrl, { width: 1024, height: 768 })

  for (const profile of profiles) {
    const context = await browser.newContext({
      ignoreHTTPSErrors: true,
      storageState,
      viewport: profile.viewport,
      screen: profile.viewport,
      hasTouch: profile.mobile,
      isMobile: profile.mobile,
    })
    try {
      const page = await context.newPage()
      for (const path of tenantRoutes) {
        await waitForResponsiveRoute(page, `${tenantBaseUrl}${path}`, `${profile.name} ${path}`)
      }
      for (const path of platformRoutes) {
        await waitForResponsiveRoute(page, `${adminBaseUrl}${path}`, `${profile.name} ${path}`)
      }
      if (profile.mobile) {
        await verifyMobileTenantNavigation(page, tenantBaseUrl)
        await verifyMobilePlatformNavigation(page, adminBaseUrl)
      }
      if (profile.name === 'mobile-375') {
        await verifyResponsivePlatformPrimaryAction(page, adminBaseUrl)
      }
    } finally {
      await context.close()
    }
  }
}

async function waitForSourceLensLogin(page, baseUrl) {
  await page.goto(`${baseUrl}/login`, { waitUntil: 'networkidle' })
  const username = page.locator(
    'input[name="username"], input[name="email"], input[autocomplete="username"], '
    + 'input[autocomplete="email"], input[type="email"], input[type="text"]',
  ).first()
  const password = page.locator(
    'input[name="password"], input[autocomplete="current-password"], input[type="password"]',
  ).first()
  // SourceLens v0.20 opens in email-code mode, where the email field is already
  // visible.  Password visibility is the stable signal that password mode is
  // active across old and new SourceLens releases.
  if (!(await password.isVisible())) {
    const switchButton = page.locator('button.block.w-full.text-center')
    await switchButton.click()
  }
  try {
    await Promise.all([
      username.waitFor({ state: 'visible', timeout: 10_000 }),
      password.waitFor({ state: 'visible', timeout: 10_000 }),
    ])
  } catch {
    const buttons = await page.locator('button').allTextContents()
    const body = (await page.locator('body').innerText()).slice(0, 1_500)
    fail(
      `SourceLens password form did not appear at ${page.url()}; `
      + `buttons=${JSON.stringify(buttons)} body=${JSON.stringify(body)}`,
    )
  }
  await username.fill(sourceLensUser)
  await password.fill(sourceLensPassword)
  await page.locator('form button[type="submit"]').click()
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 60_000 })
}

async function run() {
  const browser = await chromium.launch({ headless: true })
  try {
    const tenantBaseUrl = `https://${smokeHost}:${tenantPort}`
    const adminBaseUrl = `https://${smokeHost}:${adminPort}`
    const loginBaseUrl = `https://${smokeHost}:${loginPort}`
    const hfl = await browser.newContext({ ignoreHTTPSErrors: true, viewport: { width: 1280, height: 800 } })
    await waitForHflLogin(
      await hfl.newPage(),
      loginBaseUrl,
      '/',
    )
    await waitForPlatformOps(
      await hfl.newPage(),
      adminBaseUrl,
    )
    const storageState = await hfl.storageState()
    await hfl.close()

    await verifyResponsiveConsoles(browser, storageState, tenantBaseUrl, adminBaseUrl)

    if (!skipSourceLens) {
      const sourceLens = await browser.newContext({ ignoreHTTPSErrors: true })
      await waitForSourceLensLogin(
        await sourceLens.newPage(),
        `https://${smokeHost}:${sourceLensPort}`,
      )
      await sourceLens.close()
    }
  } finally {
    await browser.close()
  }
  process.stdout.write(
    `browser smoke: tenant, Platform Ops, responsive viewports${skipSourceLens ? '' : ', SourceLens'}${requireHmr ? ', and HMR' : ''} passed\n`,
  )
}

await run()

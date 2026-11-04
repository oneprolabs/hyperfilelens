// Start `npm run preview` first. Reuse the pinned dev-smoke Playwright package
// via PLAYWRIGHT_MODULE, or install Playwright in the caller's environment.
import assert from 'node:assert/strict'
import { after, afterEach, before, beforeEach, test } from 'node:test'

const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright')
const baseUrl = process.env.WEBSITE_BASE_URL || 'http://127.0.0.1:4174'
let browser
let context
let page
let errors

before(async () => {
  browser = await chromium.launch({
    executablePath: process.env.CHROMIUM_EXECUTABLE_PATH || undefined,
    args: process.env.CHROMIUM_NO_SANDBOX === '1' ? ['--no-sandbox'] : [],
  })
})
beforeEach(async () => {
  context = await browser.newContext({ viewport: { width: 1440, height: 900 } })
  page = await context.newPage()
  page.setDefaultTimeout(10_000)
  errors = []
  page.on('pageerror', (error) => errors.push(error.message))
  page.on('response', (response) => {
    if (response.status() >= 400 && ['script', 'stylesheet'].includes(response.request().resourceType())) {
      errors.push(`Asset HTTP ${response.status()}: ${response.url()}`)
    }
  })
})
afterEach(async () => {
  await context.close()
  assert.deepEqual(errors, [], 'navigation must not cause client-side exceptions')
})
after(async () => { await browser?.close() })

async function assertNoOverflow() {
  const dimensions = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
  }))
  assert.ok(dimensions.document <= dimensions.viewport + 1, JSON.stringify(dimensions))
}

test('desktop docs switch languages repeatedly without corrupting Vue nodes', async () => {
  await page.goto(`${baseUrl}/zh/docs/governance/`)
  const overviewTitle = await page.locator('h1').textContent()
  await page.locator('.VPSidebar a[href="/zh/docs/governance/members"]').first().click()
  await page.waitForFunction((title) => document.querySelector('h1')?.textContent !== title, overviewTitle)
  const chineseTitle = await page.locator('h1').textContent()
  const menu = page.locator('.hfl-doc-languages--bar')
  for (let cycle = 0; cycle < 3; cycle++) {
    await menu.getByRole('button').click()
    await menu.getByRole('menuitem', { name: 'English', exact: true }).click()
    await page.getByRole('heading', { name: /^Members/ }).waitFor()
    assert.equal(new URL(page.url()).pathname, '/docs/governance/members')
    await menu.getByRole('button').click()
    await menu.locator('a[hreflang="zh"]').click()
    await page.waitForFunction((title) => document.querySelector('h1')?.textContent === title, chineseTitle)
    assert.equal(new URL(page.url()).pathname, '/zh/docs/governance/members')
  }
})

test('desktop language menu supports keyboard activation and Escape', async () => {
  await page.goto(`${baseUrl}/docs/governance/groups`)
  const trigger = page.locator('.hfl-doc-languages--bar button')
  await trigger.focus()
  await page.keyboard.press('Enter')
  await page.locator('.hfl-doc-languages--bar [role="menu"]').waitFor()
  await page.keyboard.press('Escape')
  assert.equal(await trigger.getAttribute('aria-expanded'), 'false')
  assert.equal(await trigger.evaluate((element) => element === document.activeElement), true)
})

test('mobile and tablet navigation stays inside the viewport with usable language links', async () => {
  for (const width of [375, 768, 950, 1024, 1200, 1279]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`${baseUrl}/docs/governance/usage`)
    await assertNoOverflow()
    await page.getByRole('button', { name: 'Search documentation', exact: true }).waitFor()
    await page.locator('.VPNavBarHamburger').click()
    const screen = page.locator('#VPNavScreen')
    await screen.waitFor()
    await screen.getByRole('link', { name: 'Governance', exact: true }).waitFor()
    await screen.getByRole('link', { name: 'Try free', exact: true }).waitFor()
    await screen.locator('.hfl-doc-languages--screen a[hreflang="zh"]').click()
    await page.waitForFunction(() => document.documentElement.lang === 'zh-Hans'
      && !document.querySelector('h1')?.textContent?.startsWith('Usage'))
    assert.equal(new URL(page.url()).pathname, '/zh/docs/governance/usage')
    await assertNoOverflow()
  }
})

test('desktop navigation and utilities do not overflow', async () => {
  for (const width of [1280, 1366, 1440, 1920]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`${baseUrl}/docs/governance/usage`)
    await page.locator('.VPNavBarMenu').waitFor()
    await page.locator('.hfl-doc-languages--bar').waitFor()
    await assertNoOverflow()
    const outside = await page.locator([
      '.VPNavBarMenu a',
      '.hfl-doc-languages--bar button',
      '.VPNavBarSearch button',
      '.VPNavBarAppearance button',
      '.VPNavBarSocialLinks a',
      '.hfl-doc-trial--bar',
    ].join(', ')).evaluateAll((elements) => elements
      .map((element) => ({ text: element.textContent, rect: element.getBoundingClientRect() }))
      .filter(({ rect }) => rect.width > 0 && (rect.left < -1 || rect.right > innerWidth + 1))
      .map(({ text, rect }) => ({ text, left: rect.left, right: rect.right })))
    assert.deepEqual(outside, [], `header controls must fit at ${width}px`)
    assert.equal(await page.locator('.hfl-doc-languages--bar svg').first()
      .evaluate((element) => getComputedStyle(element).fill), 'none')
  }
})

test('home language links update after section changes, including a hash on initial load', async () => {
  await page.goto(`${baseUrl}/zh/#contact`)
  const links = page.locator('.site-header .lang-switcher-item')
  await page.waitForFunction(() => document.querySelector('.site-header .lang-switcher-item')?.getAttribute('href') === '/#contact')
  await page.locator('.site-header nav a[href="#use-cases"]').click()
  await page.waitForFunction(() => document.querySelector('.site-header .lang-switcher-item')?.getAttribute('href') === '/#use-cases')
  assert.deepEqual(await links.evaluateAll((items) => items.map((item) => item.getAttribute('href'))),
    ['/#use-cases', '/zh/#use-cases', '/es/#use-cases'])
  await page.locator('.site-header .lang-switcher-trigger').click()
  await links.filter({ hasText: 'English' }).click()
  await page.waitForURL(`${baseUrl}/#use-cases`)
})

test('docs retain Chinese-only page fallback and blog links retain their post', async () => {
  await page.goto(`${baseUrl}/zh/docs/backup-restore/prepare`)
  const docs = page.locator('.hfl-doc-languages--bar')
  await docs.getByRole('button').click()
  assert.equal(await docs.getByRole('menuitem', { name: 'English', exact: true }).getAttribute('href'), '/docs/product')
  await docs.getByRole('menuitem', { name: 'English', exact: true }).click()
  await page.waitForURL((url) => url.pathname.replace(/\/$/, '') === '/docs/product')
  await page.goto(`${baseUrl}/zh/blog/posts/backups-help-ai-understand-business`)
  await page.locator('.site-header .lang-switcher-trigger').click()
  const english = page.locator('.site-header .lang-switcher-item').filter({ hasText: 'English' })
  assert.equal(await english.getAttribute('href'), '/blog/posts/backups-help-ai-understand-business')
  await english.click()
  await page.waitForURL(`${baseUrl}/blog/posts/backups-help-ai-understand-business`)
})

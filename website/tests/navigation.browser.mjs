// Start `npm run preview` first. Reuse the pinned dev-smoke Playwright package
// via PLAYWRIGHT_MODULE, or install Playwright in the caller's environment.
import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { after, afterEach, before, beforeEach, test } from 'node:test'

const zhCopy = JSON.parse(await readFile(
  new URL('../zh/.test-fixtures/managed-storage.json', import.meta.url), 'utf8',
))
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

async function assertGuideLanguageLinks(route, heading) {
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 900 })
    await page.goto(`${baseUrl}/zh${route}`)
    const chineseTitle = await page.locator('h1').textContent()

    async function openLanguages() {
      if (width >= 1280) {
        const menu = page.locator('.hfl-doc-languages--bar')
        await menu.getByRole('button').click()
        return menu
      }
      const trigger = page.locator('.VPNavBarHamburger')
      if (await trigger.getAttribute('aria-expanded') !== 'true') await trigger.click()
      const menu = page.locator('.hfl-doc-languages--screen')
      await menu.waitFor()
      return menu
    }

    let menu = await openLanguages()
    assert.equal(await menu.locator('a[hreflang="en"]').getAttribute('href'), route)
    await menu.locator('a[hreflang="en"]').click()
    await page.waitForURL((url) => url.pathname === route)
    await page.getByRole('heading', { name: heading }).waitFor()

    menu = await openLanguages()
    const current = menu.locator('a[hreflang="en"]')
    assert.equal(await current.getAttribute('aria-current'), 'page')
    assert.equal(await current.getAttribute('href'), route)
    await current.click()
    assert.equal(new URL(page.url()).pathname, route)

    menu = await openLanguages()
    assert.equal(await menu.locator('a[hreflang="zh"]').getAttribute('href'), `/zh${route}`)
    await menu.locator('a[hreflang="zh"]').click()
    await page.waitForURL((url) => url.pathname === `/zh${route}`)
    await page.waitForFunction((title) => document.querySelector('h1')?.textContent === title, chineseTitle)
    await assertNoOverflow()
  }
}

for (const { name, route, heading } of [
  {
    name: 'recovery guide',
    route: '/docs/backup-restore/recover-to-new-host',
    heading: /^Recover data to a new host/,
  },
  {
    name: 'repository cache guide',
    route: '/docs/deployment/repository-cache',
    heading: /^Repository cache settings/,
  },
  {
    name: 'managed storage overview',
    route: '/docs/deployment/personal-edition/managed-storage/',
    heading: /^Managed Storage Overview/,
  },
  {
    name: 'managed Alibaba Cloud guide',
    route: '/docs/deployment/personal-edition/managed-storage/aliyun',
    heading: /^Connect Alibaba Cloud OSS/,
  },
]) {
  test(`${name} language links retain the guide on desktop and mobile`, async () => {
    await assertGuideLanguageLinks(route, heading)
  })
}

test('managed storage guides expose ordered navigation, scoped JSON, and configuration checklists on both layouts', async () => {
  const directory = '/docs/deployment/personal-edition/managed-storage'
  const providers = ['huawei', 'aliyun', 'aws', 'rustfs', 'minio']
  for (const prefix of ['', '/zh']) {
    for (const width of [1440, 375]) {
      await page.setViewportSize({ width, height: 900 })
      for (const provider of providers) {
        await page.goto(`${baseUrl}${prefix}${directory}/${provider}`)
        await page.locator('.vp-doc h1').waitFor()
        const navigation = page.locator('.VPSidebar')
        await navigation.locator('.VPSidebarItem.level-0 > .item .text').last()
          .locator('.hfl-sidebar-icon svg').waitFor({ state: 'attached' })
        for (const item of providers) {
          assert.equal(await navigation.locator(`a[href="${prefix}${directory}/${item}"]`).count(), 1)
        }
        await page.locator('.vp-doc div[class*="language-json"] pre').first().waitFor()
        const body = await page.locator('.vp-doc').textContent()
        assert.match(body, /personal\/repositories\/\*/)
        if (prefix) {
          assert.ok(body.includes(zhCopy.checklistHeading))
          assert.ok(body.includes(zhCopy.configurationDetailsHeading))
          for (const term of zhCopy.internalCommunicationTerms) {
            assert.ok(!body.includes(term), `published guide must not contain ${term}`)
          }
        } else {
          assert.match(body, /Configuration Checklist/)
          assert.match(body, /Required Configuration Details/)
          assert.doesNotMatch(body, /colleague|teammate|handoff|Please prepare|Return:/i)
        }
        const warning = page.locator('.vp-doc .custom-block.warning').filter({
          hasText: prefix ? zhCopy.setupWarningTitle : 'Initial setup and personal registration',
        })
        assert.equal(await warning.count(), 1)
        const warningText = await warning.textContent()
        if (prefix) assert.ok(warningText.includes(zhCopy.automaticDefaultSentence))
        else assert.match(warningText,
          /when no default connection exists, saving a new connection automatically makes it the default/)
        await assertNoOverflow()
      }
    }
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
    await screen.getByRole('link', { name: 'Organization Management', exact: true }).waitFor()
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
  const nativeLinks = await page.locator('.VPNavBarTranslations a, .VPNavBarExtra .translations a')
    .evaluateAll((links) => links.map((link) => link.getAttribute('href')))
  assert.ok(nativeLinks.length > 0)
  assert.ok(nativeLinks.every((href) => ['/', '/zh/', '/es/'].includes(href)),
    'hidden native menus must not publish missing translations')
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

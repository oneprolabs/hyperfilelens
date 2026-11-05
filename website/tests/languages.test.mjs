import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import test from 'node:test'
import { transformWithEsbuild } from 'vite'

const source = await readFile(new URL('../.vitepress/theme/languages.ts', import.meta.url), 'utf8')
const { code } = await transformWithEsbuild(source, 'languages.ts', { loader: 'ts' })
const { localizedDocPath, localizedSitePath, pathWithoutLocale } = await import(
  `data:text/javascript;base64,${Buffer.from(code).toString('base64')}`
)

test('locale prefixes only match complete path segments', () => {
  assert.equal(pathWithoutLocale('/zh/docs/governance/'), '/docs/governance/')
  assert.equal(pathWithoutLocale('/es'), '/')
  assert.equal(pathWithoutLocale('/zhello/'), '/zhello/')
})

test('home language links preserve the current section', () => {
  assert.equal(localizedSitePath('/zh/', 'en', '#use-cases'), '/#use-cases')
  assert.equal(localizedSitePath('/', 'zh', '#contact'), '/zh/#contact')
  assert.equal(localizedSitePath('/es/', 'zh', ''), '/zh/')
})

test('blog language links retain the post instead of returning home', () => {
  assert.equal(localizedSitePath('/zh/blog', 'en'), '/blog/')
  assert.equal(localizedSitePath('/blog/posts/example/', 'es'), '/es/blog/posts/example')
  assert.equal(localizedSitePath('/es/blog/posts/example', 'zh'), '/zh/blog/posts/example')
})

test('documentation switches language on the same page', () => {
  for (const slug of ['organization', 'members', 'groups', 'roles', 'resources', 'usage']) {
    assert.equal(localizedDocPath(`/zh/docs/governance/${slug}`, 'en'), `/docs/governance/${slug}`)
    assert.equal(localizedDocPath(`/docs/governance/${slug}`, 'zh'), `/zh/docs/governance/${slug}`)
  }
  assert.equal(localizedDocPath('/zh/docs/governance/', 'en'), '/docs/governance')
  assert.equal(localizedDocPath('/docs/', 'zh'), '/zh/docs')
  assert.equal(localizedDocPath('/zh/docs/faq/backup-scope', 'en'), '/docs/faq/backup-scope')
})

test('recovery guide links retain the guide in both languages', () => {
  const route = '/docs/backup-restore/recover-to-new-host'
  assert.equal(localizedDocPath(`/zh${route}`, 'en'), route)
  assert.equal(localizedDocPath(route, 'en'), route)
  assert.equal(localizedDocPath(route, 'zh'), `/zh${route}`)
})

test('every published English document is recognized by the language switcher', async () => {
  const files = await readdir(new URL('../en/docs/', import.meta.url), { recursive: true })
  const markdownFiles = files.filter((file) => file.endsWith('.md'))
  assert.ok(markdownFiles.length > 0, 'the documentation catalog must not be empty')

  for (const file of markdownFiles) {
    const slug = file.replaceAll('\\', '/').replace(/(^|\/)index\.md$/, '').replace(/\.md$/, '')
    const route = `/docs/${slug}`.replace(/\/+$/, '')
    for (const input of [route, `${route}/`, `/zh${route}`, `/zh${route}/`]) {
      assert.equal(localizedDocPath(input, 'en'), route, `${input} must retain its English document`)
      assert.equal(localizedDocPath(input, 'zh'), `/zh${route}`, `${input} must retain its Chinese document`)
    }
  }
})

test('Chinese-only documentation uses a published English fallback', () => {
  assert.equal(localizedDocPath('/zh/docs/backup-restore/prepare', 'en'), '/docs/product')
  assert.equal(localizedDocPath('/zh/docs/getting-started/requirements', 'en'), '/docs')
})

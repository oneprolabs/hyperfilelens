import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
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

test('Chinese-only documentation uses a published English fallback', () => {
  assert.equal(localizedDocPath('/zh/docs/backup-restore/prepare', 'en'), '/docs/product')
  assert.equal(localizedDocPath('/zh/docs/getting-started/requirements', 'en'), '/docs')
})

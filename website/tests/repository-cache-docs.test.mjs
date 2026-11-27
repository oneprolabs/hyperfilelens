import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const root = new URL('../', import.meta.url)
const forbiddenProductName = [107, 111, 112, 105, 97].map((code) => String.fromCharCode(code)).join('')
const docs = {
  en: await readFile(new URL('en/docs/deployment/repository-cache.md', root), 'utf8'),
  zh: await readFile(new URL('zh/docs/deployment/repository-cache.md', root), 'utf8'),
}

for (const [locale, source] of Object.entries(docs)) {
  test(`${locale} cache guide documents the managed cache contract`, () => {
    for (const phrase of ['cache_management_v1', '10 GiB', '20 GiB', 'CACHE_SPACE_INSUFFICIENT', 'CACHE_SPACE_CHECK_FAILED']) {
      assert.ok(source.includes(phrase), `${locale}: missing ${phrase}`)
    }
    assert.match(source, /cache\/repositories\//)
    assert.doesNotMatch(source, new RegExp(forbiddenProductName, 'i'))
  })
}

test('the guide keeps legacy fixed-budget behavior explicitly scoped to older nodes', () => {
  assert.match(docs.en, /Older-node compatibility[\s\S]*8192 MB[\s\S]*0–65536 MB[\s\S]*75% content cache and 25% metadata cache/)
  assert.match(docs.zh, /8192 MB[\s\S]*0～65536 MB[\s\S]*75%[\s\S]*25%/)
})

test('deployment pages link to the cache guide', async () => {
  for (const locale of ['en', 'zh']) {
    for (const page of ['agent', 'proxy', 'index']) {
      const source = await readFile(new URL(`${locale}/docs/deployment/${page}.md`, root), 'utf8')
      assert.match(source, /repository-cache/)
    }
  }
})

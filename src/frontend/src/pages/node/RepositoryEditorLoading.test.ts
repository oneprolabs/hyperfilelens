import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { compileTemplate, parse } from '@vue/compiler-sfc'

const pages = ['EditS3Repo.vue', 'EditProxyFsRepo.vue', 'RepairNasRepository.vue']

describe('repository editor initialization', () => {
  it.each(pages)('%s keeps the target layout visible under an initialization overlay', (filename) => {
    const source = readFileSync(new URL(`./${filename}`, import.meta.url), 'utf8')
    const { descriptor } = parse(source)
    const template = descriptor.template!.content

    expect(source).toContain('const loading = ref(true)')
    expect(template).toMatch(/v-loading="loading"\s+class="fullscreen-form-layout/)
    expect(template).toContain(':aria-busy="loading"')
    expect(template).toContain(':element-loading-text="t(\'common.loading\')"')
    expect(template).toContain(':inert="loading ? true : undefined"')
    expect(template).not.toContain('v-if="loading"')
    expect(template).not.toMatch(/class="edit-(?:s3|proxy-fs)-loading"/)
    expect(template).toMatch(/:loading="busy"\s+:disabled="loading \|\| busy/)
    expect(compileTemplate({ source: template, filename, id: filename }).errors).toEqual([])
  })

  it.each(pages.slice(0, 2))('%s renders the form before repository data arrives', (filename) => {
    const source = readFileSync(new URL(`./${filename}`, import.meta.url), 'utf8')
    expect(source).toContain('v-if="loading || repo"')
    expect(source).not.toContain('v-else-if="repo"')
    expect(source).toContain("router.replace('/node/repositories')")
  })

  it('preserves the NAS repair error state instead of exposing an empty editable form on failure', () => {
    const source = readFileSync(new URL('./RepairNasRepository.vue', import.meta.url), 'utf8')
    expect(source).toContain('v-if="loadError"')
    expect(source).toContain('v-if="!loadError"')
  })
})

import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { compileStyle, parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

const skinPath = resolve(process.cwd(), 'src/styles/s3-object-storage-form.css')
const skin = readFileSync(skinPath, 'utf8')

describe('object storage form presentation', () => {
  it('keeps the repository form on the shared scoped skin without local overrides', () => {
    const source = readFileSync(resolve(process.cwd(), 'src/pages/node/AddS3Repo.vue'), 'utf8')
    const { descriptor } = parse(source)
    expect(descriptor.styles).toEqual(expect.arrayContaining([
      expect.objectContaining({ scoped: true, src: '../../styles/s3-object-storage-form.css' }),
    ]))
    for (const style of descriptor.styles.filter(style => !style.src)) {
      expect(style.content).not.toMatch(/\.add-form-preview-|\.add-s3-region-|\.add-s3-search/)
    }
  })

  it('scopes the skin while preserving theme, hover, selected and Element Plus selectors', () => {
    const result = compileStyle({ source: skin, filename: skinPath, id: 'data-v-storage-form', scoped: true })
    expect(result.errors).toEqual([])
    expect(result.code).toContain('.add-form-preview-card[data-v-storage-form]')
    expect(result.code).toContain('.add-s3-region-btn[data-v-storage-form]:hover')
    expect(result.code).toContain('.add-s3-region-btn--active[data-v-storage-form]')
    expect(result.code).toContain(':root:not([data-theme="dark"]) .add-form-preview-card[data-v-storage-form]')
    expect(result.code).toContain('.add-s3-element-field[data-v-storage-form] .el-input__wrapper')
    expect(result.code).toContain('.add-s3-search[data-v-storage-form] .el-input__wrapper')
    expect(skin).not.toContain('@import')
  })
})

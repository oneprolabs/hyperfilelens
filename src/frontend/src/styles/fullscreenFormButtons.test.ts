import { readdirSync, readFileSync } from 'node:fs'
import { relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { compactSourceText } from '../test/sourceText'

function frontendSourcesBelow(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return frontendSourcesBelow(path)
    return /\.(?:css|vue)$/.test(path) ? [path] : []
  })
}

describe('fullscreen form buttons', () => {
  it('uses Element Plus buttons instead of the legacy form action spinner', () => {
    const sourceRoot = resolve(process.cwd(), 'src')
    const legacyAction = /form-action(?:--|__|\s|")|form-action-spin/
    const localImplementations = frontendSourcesBelow(sourceRoot)
      .filter((file) => legacyAction.test(readFileSync(file, 'utf8')))
      .map((file) => relative(sourceRoot, file))
      .sort()

    expect(localImplementations).toEqual([])
  })

  it('binds the Add S3 primary action to the shared loading behavior', () => {
    const addS3Repo = compactSourceText(
      readFileSync(resolve(process.cwd(), 'src/pages/node/AddS3Repo.vue'), 'utf8'),
    )

    expect(addS3Repo).toContain('<ElButton type="primary" :loading="busy" :disabled="!canSubmit" @click="onSubmit">')
    expect(addS3Repo).not.toContain('form-action__loading')
  })
})

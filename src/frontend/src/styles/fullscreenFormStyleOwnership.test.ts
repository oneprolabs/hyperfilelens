import { readdirSync, readFileSync } from 'node:fs'
import { relative, resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const sourceRoot = resolve(process.cwd(), 'src')
const sharedStyleEntry = resolve(sourceRoot, 'styles/fullscreen-form-styles.ts')

function vueSourcesBelow(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap((entry) => {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) return vueSourcesBelow(path)
    return path.endsWith('.vue') ? [path] : []
  })
}

describe('fullscreen form style ownership', () => {
  it('loads shared fullscreen form styles through an unscoped script entry', () => {
    expect(readFileSync(sharedStyleEntry, 'utf8')).toBe([
      "import './fullscreen-form-shell.css'",
      "import './resource-add.css'",
      '',
    ].join('\n'))
  })

  it('does not process shared global styles through Vue style blocks', () => {
    const externalStylePattern = /<style[^>]+src=["'][^"']*(?:fullscreen-form-shell|resource-add)\.css["'][^>]*>/
    const offenders = vueSourcesBelow(sourceRoot)
      .filter((file) => externalStylePattern.test(readFileSync(file, 'utf8')))
      .map((file) => relative(sourceRoot, file))
      .sort()

    expect(offenders).toEqual([])
  })

  it('keeps every migrated consumer on the route-loadable shared entry', () => {
    const consumers = vueSourcesBelow(sourceRoot)
      .filter((file) => readFileSync(file, 'utf8').includes("styles/fullscreen-form-styles'"))
      .map((file) => relative(sourceRoot, file))
      .sort()

    expect(consumers).toEqual([
      'pages/insight/AiModelFormPage.vue',
      'pages/insight/KnowledgeSourceFormPage.vue',
      'pages/insight/NewCopilotChat.vue',
      'pages/node/AddNasRepository.vue',
      'pages/node/AddProxyFsRepository.vue',
      'pages/node/AddS3Repo.vue',
      'pages/node/EditProxyFsRepo.vue',
      'pages/node/EditS3Repo.vue',
      'pages/node/NodesDeploy.vue',
      'pages/node/RepairNasRepository.vue',
      'pages/ops/AlertPolicyEditorPage.vue',
      'pages/ops/NotificationChannelEditorPage.vue',
      'pages/protection/BackupCreateWizard.vue',
      'pages/protection/BackupSources.vue',
      'pages/protection/DataProtection.vue',
      'pages/protection/PolicyEditorPage.vue',
      'platform-ops/pages/engine/PlatformGatewayAdd.vue',
    ])
  })
})

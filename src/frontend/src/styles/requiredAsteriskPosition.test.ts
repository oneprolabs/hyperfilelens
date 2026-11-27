import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { parse as parseTemplate, NodeTypes, type ElementNode, type RootNode, type TemplateChildNode } from '@vue/compiler-dom'
import { parse } from '@vue/compiler-sfc'
import { describe, expect, it } from 'vitest'

function formsIn(file: string) {
  const source = readFileSync(resolve(process.cwd(), 'src', file), 'utf8')
  const { descriptor } = parse(source)
  const forms: ElementNode[] = []
  function visit(node: RootNode | TemplateChildNode) {
    if (node.type === NodeTypes.ELEMENT && ['el-form', 'ElForm'].includes(node.tag)) forms.push(node)
    if (node.type === NodeTypes.ROOT || node.type === NodeTypes.ELEMENT) node.children.forEach(visit)
  }
  visit(parseTemplate(descriptor.template!.content))
  return forms
}

function attribute(form: ElementNode, name: string) {
  const prop = form.props.find(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === name)
  return prop?.type === NodeTypes.ATTRIBUTE ? prop.value?.content : undefined
}

describe('required asterisks follow field labels', () => {
  it.each([
    ['components/ChangeProxyHostDialog.vue', 'hfl-flow-action-dialog__form'],
    ['pages/insight/InsightDataGateways.vue', 'dg-capacity-dialog__form'],
    ['pages/node/EditProxyFsRepo.vue', 'add-proxy-fs-form'],
    ['pages/node/Nodes.vue', 'proxy-host-edit-form'],
    ['pages/protection/BackupCreateWizard.vue', 'target-batch-form'],
    ['pages/protection/BackupSources.vue', 'source-action-dialog__form'],
    ['pages/settings/SubscriptionContent.vue', 'subscription-request-dialog__form'],
  ])('uses the native trailing marker in %s', (file, formClass) => {
    const forms = formsIn(file).filter(form =>
      attribute(form, 'class')?.split(/\s+/).includes(formClass)
      && /\srequired(?=[\s>])/.test(form.loc.source),
    )
    expect(forms).toHaveLength(1)
    expect(attribute(forms[0]!, 'require-asterisk-position')).toBe('right')
    expect(forms[0]!.loc.source).toContain('required')
    expect(forms[0]!.props.some(prop => prop.type === NodeTypes.ATTRIBUTE && prop.name === 'hide-required-asterisk')).toBe(false)
  })

  it('places the Repair NAS display-name marker after its label without changing other sections', () => {
    const forms = formsIn('pages/node/RepairNasRepository.vue')
    const form = forms.find(form => form.loc.source.includes('data-validation-field="displayName"'))!
    expect(form).toBeDefined()
    expect(attribute(form, 'require-asterisk-position')).toBe('right')
    expect(form.loc.source).toMatch(/:label="[^"]+"\s+required/)
  })

  it('places the source-tag name marker after the label', () => {
    const forms = formsIn('pages/protection/SourceTags.vue')
    expect(forms).toHaveLength(1)
    expect(attribute(forms[0]!, 'require-asterisk-position')).toBe('right')
    expect(forms[0]!.loc.source).toContain('required')
  })

  it.each([
    'styles/source-list-ui.css',
    'components/backupSourceFlowActionDialog.css',
    'pages/protection/BackupSources.vue',
  ])('does not retain obsolete leading-marker overrides in %s', file => {
    const source = readFileSync(resolve(process.cwd(), 'src', file), 'utf8')
    expect(source).not.toMatch(/\.el-form-item\.is-required[^{}]*\.el-form-item__label::before/)
  })

  it('preserves existing fullscreen suffix styling and the manually marked Proxy Host row', () => {
    const skin = readFileSync(resolve(process.cwd(), 'src/styles/resource-add.css'), 'utf8')
    expect(skin).toMatch(/\.el-form-item\.is-required:not\(\.is-no-asterisk\)[^{]*\.el-form-item__label::before\s*\{\s*content:\s*none/)
    expect(skin).toMatch(/\.el-form-item\.is-required:not\(\.is-no-asterisk\)[^{]*\.el-form-item__label::after\s*\{\s*content:\s*'\*'/)
    const source = readFileSync(resolve(process.cwd(), 'src/pages/node/AddProxyFsRepository.vue'), 'utf8')
    expect(source).toContain('add-proxy-fs-bind-form-item is-no-asterisk')
    expect(source).toContain("{{ t('repositoriesPage.fieldProxyNode') }}<span class=\"add-proxy-fs-proxy-label__required\">*</span>")
  })
})

// @vitest-environment jsdom
import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { ElButton, ElCheckbox, ElCheckboxGroup, ElInput, ElEmpty } from 'element-plus'
import { createI18n } from 'vue-i18n'
import SourceTagActionContent from './SourceTagActionContent.vue'
import { en } from '../locales/en'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const catalogs = Object.fromEntries(['zh-hans', 'es'].map((language) => [
  language,
  JSON.parse(readFileSync(resolve(process.cwd(), `../../language-packs/packs/${language}/frontend/messages.json`), 'utf8')),
]))

const wrappers: Array<ReturnType<typeof mount>> = []
function setup(overrides = {}, locale = 'en', missing = () => undefined) {
  const wrapper = mount(SourceTagActionContent, {
    props: {
      sources: [{ id: 'agent:1', name: 'Host A' }, { id: 'nas:2', name: 'NAS B' }],
      options: [{ id: 1, name: 'Long tag '.repeat(8), description: 'Full description '.repeat(20), color: 'blue' }],
      operation: 'add', affected: 2, loading: false, saving: false,
      ids: [], search: '', ...overrides,
    },
    global: {
      plugins: [createI18n({ legacy: false, locale, fallbackLocale: 'en', messages: { en, ...catalogs }, missing })],
      components: { ElButton, ElCheckbox, ElCheckboxGroup, ElInput, ElEmpty },
      directives: { loading: (el, binding) => { el.dataset.loading = String(binding.value) } },
      stubs: { RouterLink: { template: '<a><slot /></a>' } },
    },
  })
  wrappers.push(wrapper)
  return wrapper
}
afterEach(() => wrappers.splice(0).forEach((wrapper) => wrapper.unmount()))

describe('tag action content', () => {
  it('aligns the management link with the surrounding text baseline', () => {
    const styles = readFileSync(resolve(process.cwd(), 'src/styles/source-tag-ui.css'), 'utf8')
    const linkRule = styles.split('.source-tag-action__manage-hint .source-tag-wizard-manage-link {')[1]?.split('}')[0]
    expect(linkRule).toContain('display: inline;')
    expect(linkRule).toContain('margin-inline: 3px;')
    expect(linkRule).toContain('line-height: inherit;')
    expect(linkRule).toContain('vertical-align: baseline;')
    expect(linkRule).not.toContain('inline-flex')
  })

  it.each(['zh-hans', 'es'])('renders all dialog guidance in %s without falling back to English', async (locale) => {
    const missingKeys: string[] = []
    const wrapper = setup({ wizard: true }, locale, (_language?: string, key?: string) => {
      if (key) missingKeys.push(key)
      return undefined
    })
    const tags = catalogs[locale].protection.tags
    expect(wrapper.find('.source-tag-action__targets button').text()).toBe(tags.viewSelectedSources)
    expect(wrapper.find('input.el-input__inner').attributes('placeholder')).toBe(tags.searchTags)
    expect(wrapper.find('a').text()).toBe(tags.managementLink)
    expect(wrapper.find('.source-tag-action__manage-hint').text()).toBe(tags.bindManageHint.replace('{link}', tags.managementLink))
    expect(wrapper.text()).toContain(tags.wizardSaveHint)
    expect(wrapper.find('.source-tag-bulk-impact').text()).toBe(tags.selectTagsToBind)
    await wrapper.find('.source-tag-action__targets button').trigger('click')
    expect(wrapper.find('.source-tag-action__targets button').text()).toBe(tags.hideSelectedSources)
    await wrapper.setProps({ ids: [1] })
    expect(wrapper.find('.source-tag-bulk-impact').text()).toBe(tags.bindSummary.replace('{tags}', '1').replace('{associations}', '2'))
    await wrapper.setProps({ operation: 'remove' })
    expect(wrapper.find('.source-tag-action__manage-hint').text()).toBe(tags.unbindManageHint.replace('{link}', tags.managementLink))
    expect(wrapper.find('.source-tag-bulk-impact').text()).toBe(tags.unbindSummary.replace('{tags}', '1').replace('{associations}', '2'))
    expect(wrapper.text()).toContain(tags.unbindPreservesHint)
    await wrapper.setProps({ affected: 0 })
    expect(wrapper.text()).toContain(tags.noAssociationChanges)
    await wrapper.setProps({ options: [], ids: [] })
    expect(wrapper.text()).toContain(tags.noMatchingTags)
    expect(wrapper.text()).toContain(tags.selectTagsToUnbind)
    expect(missingKeys).toEqual([])
  })

  it('collapses selected sources by default and lets users inspect every target', async () => {
    const wrapper = setup()
    expect(wrapper.find('.source-tag-bulk-targets').exists()).toBe(false)
    const button = wrapper.find('.source-tag-action__targets button')
    expect(button.attributes('aria-expanded')).toBe('false')
    await button.trigger('click')
    expect(wrapper.findAll('li').map((item) => item.text())).toEqual(['Host A', 'NAS B'])
    expect(button.attributes('aria-expanded')).toBe('true')
    await button.trigger('click')
    expect(wrapper.findAll('li')).toHaveLength(0)
  })

  it('renders complete tag names and descriptions without decorative tag icons', () => {
    const wrapper = setup()
    expect(wrapper.find('.source-tag-badge__label').text()).toBe('Long tag '.repeat(8).trim())
    expect(wrapper.find('.source-tag-picker__description').text()).toBe('Full description '.repeat(20).trim())
    expect(wrapper.find('.source-tag-badge__icon').exists()).toBe(false)
  })

  it('emits tag and search changes and summarizes the actual association impact', async () => {
    const wrapper = setup()
    await wrapper.find('input[type="checkbox"]').setValue(true)
    expect(wrapper.emitted('update:ids')?.[0]).toEqual([[1]])
    await wrapper.find('input.el-input__inner').setValue('production')
    expect(wrapper.emitted('update:search')?.[0]).toEqual(['production'])
    await wrapper.setProps({ ids: [1] })
    expect(wrapper.find('.source-tag-bulk-impact').text()).toContain('2 associations will be added')
    await wrapper.setProps({ operation: 'remove', affected: 1 })
    expect(wrapper.find('.source-tag-bulk-impact').text()).toContain('1 associations will be removed')
    expect(wrapper.text()).toContain('Other tags and backup configurations are unchanged')
  })

  it('uses a quiet wizard-only save note and a secondary management link', async () => {
    const wrapper = setup()
    expect(wrapper.text()).not.toContain('Canceling the backup wizard')
    await wrapper.setProps({ wizard: true })
    expect(wrapper.text()).toContain('Canceling the backup wizard will not undo them')
    expect(wrapper.find('.el-alert').exists()).toBe(false)
    expect(wrapper.find('a').attributes('target')).toBe('_blank')
    expect(wrapper.find('a').attributes('rel')).toBe('noopener noreferrer')
    expect(wrapper.findAll('a')).toHaveLength(1)
    expect(wrapper.find('.source-tag-action__notes a').exists()).toBe(false)
    const children = wrapper.find('.source-tag-action').element.children
    expect(Array.from(children).indexOf(wrapper.find('.source-tag-action__manage-hint').element))
      .toBeLessThan(Array.from(children).indexOf(wrapper.find('.el-input').element))
  })

  it('disables editing while loading or saving and keeps loading distinct from empty', async () => {
    const wrapper = setup({ loading: true, options: [] })
    expect(wrapper.find('.source-tag-picker').attributes('data-loading')).toBe('true')
    expect(wrapper.find('.el-empty').exists()).toBe(false)
    expect(wrapper.find('input.el-input__inner').attributes('disabled')).toBeDefined()
    await wrapper.setProps({ loading: false })
    expect(wrapper.find('.el-empty').exists()).toBe(true)
    await wrapper.setProps({ saving: true })
    expect(wrapper.find('input.el-input__inner').attributes('disabled')).toBeDefined()
  })

  it('resets the expanded target list when a new operation selects other sources', async () => {
    const wrapper = setup()
    await wrapper.find('.source-tag-action__targets button').trigger('click')
    await wrapper.setProps({ sources: [{ id: 'agent:3', name: 'Host C' }] })
    expect(wrapper.find('.source-tag-bulk-targets').exists()).toBe(false)
  })

  it('allows deselection at the fifty-tag limit while disabling additional tags', () => {
    const wrapper = setup({
      ids: Array.from({ length: 50 }, (_, index) => index + 1),
      options: [
        { id: 1, name: 'Selected', description: '', color: 'blue' },
        { id: 51, name: 'Additional', description: '', color: 'neutral' },
      ],
    })
    const checkboxes = wrapper.findAll('input[type="checkbox"]')
    expect(checkboxes[0].attributes('disabled')).toBeUndefined()
    expect(checkboxes[1].attributes('disabled')).toBeDefined()
  })
})

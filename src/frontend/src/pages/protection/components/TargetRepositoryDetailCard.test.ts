// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import ElementPlus, { ElTag } from 'element-plus'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../../../locales/en'
import TargetRepositoryDetailCard from './TargetRepositoryDetailCard.vue'
import type { TargetRepositoryItem } from './TargetRepositoryPicker.vue'

function render(target: Partial<TargetRepositoryItem>) {
  return mount(TargetRepositoryDetailCard, {
    props: {
      target: { id: 'test', name: 'Test repository', location: '/backup', repoType: 'S3', ...target },
    },
    global: {
      plugins: [ElementPlus, createI18n({ legacy: false, locale: 'en', messages: { en } })],
    },
  })
}

describe('TargetRepositoryDetailCard tags', () => {
  it.each(['S3', 'NAS', 'PROXY_FS'])('uses the original borderless type tag for %s repositories', (repoType) => {
    const wrapper = render({ repoType, health: 'online' })
    const tag = wrapper.findAllComponents(ElTag)[0]!
    expect(tag.props()).toMatchObject({ size: 'small', effect: 'plain' })
    expect(tag.classes()).toContain('wizard-repo-type-tag')
    expect(tag.classes()).toContain(`wizard-repo-type-tag--${repoType.toLowerCase()}`)
    wrapper.unmount()
  })

  it.each(['nfs', 'smb'])('uses the original borderless tag for the %s protocol', (nasProtocol) => {
    const wrapper = render({ repoType: 'NAS', nasProtocol, health: 'online' })
    const tag = wrapper.findAllComponents(ElTag)[1]!
    expect(tag.props()).toMatchObject({ size: 'small', effect: 'plain' })
    expect(tag.classes()).toContain('wizard-protocol-tag')
    expect(tag.classes()).toContain(`wizard-protocol-tag--${nasProtocol}`)
    wrapper.unmount()
  })

  it.each([
    ['online', 'success'],
    ['unverified', 'warning'],
    ['offline', 'danger'],
  ])('preserves the list health tag semantics for %s', (health, type) => {
    const wrapper = render({ health })
    const tag = wrapper.findAllComponents(ElTag).at(-1)!
    expect(tag.props()).toMatchObject({ size: 'small', effect: 'light', type })
    wrapper.unmount()
  })
})

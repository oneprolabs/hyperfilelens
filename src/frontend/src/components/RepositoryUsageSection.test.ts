// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import RepositoryUsageSection from './RepositoryUsageSection.vue'

function mountSection(props = {}) {
  return mount(RepositoryUsageSection, {
    props: { refreshing: false, lastSuccess: '—', ...props },
    slots: { default: '<div class="usage-value">2.4 GiB</div>' },
    global: {
      plugins: [createI18n({ legacy: false, locale: 'en', messages: { en } })],
      stubs: {
        ElButton: { props: ['disabled', 'loading'], template: '<button :disabled="disabled"><slot /></button>' },
        ElTag: { props: ['type'], template: '<span class="status-tag" :data-type="type"><slot /></span>' },
      },
    },
  })
}

describe('shared repository usage section', () => {
  it.each([
    ['success', 'success', en.repositoriesPage.usageMeasurementSuccess],
    ['failed', 'danger', en.repositoriesPage.usageMeasurementFailed],
    ['pending', 'info', en.repositoriesPage.usageMeasurementPending],
  ])('renders %s as a semantic status tag', (status, tone, label) => {
    const wrapper = mountSection({ status })
    expect(wrapper.get('.status-tag').text()).toBe(label)
    expect(wrapper.get('.status-tag').attributes('data-type')).toBe(tone)
    expect(wrapper.get('.usage-value').text()).toBe('2.4 GiB')
    expect(wrapper.text()).toContain(en.repositoriesPage.usageLastUpdated)
    wrapper.unmount()
  })

  it('emits refresh and distinguishes running measurement from pending', async () => {
    const wrapper = mountSection({ status: 'failed', error: 'Proxy offline' })
    expect(wrapper.get('.repository-usage-section__notice-reason').text()).toBe('Proxy offline')
    expect(wrapper.get('.repository-usage-section__notice-help').text()).toBe(en.repositoriesPage.usageRefreshFailedHelp)
    await wrapper.get('button').trigger('click')
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    await wrapper.setProps({ refreshing: true })
    expect(wrapper.get('button').attributes('disabled')).toBeDefined()
    expect(wrapper.get('.status-tag').text()).toBe(en.repositoriesPage.usageRefreshPending)
    expect(wrapper.find('.repository-usage-section__notice').exists()).toBe(false)
    wrapper.unmount()
  })

  it('localizes an unavailable repository owner without a large alert', () => {
    const wrapper = mountSection({ status: 'failed', error: 'Repository owner is unavailable.' })
    expect(wrapper.get('[role="status"]').text()).toContain(en.repositoriesPage.usageOwnerUnavailable)
    expect(wrapper.text()).not.toContain('Repository owner is unavailable.')
    expect(wrapper.find('el-alert').exists()).toBe(false)
    wrapper.unmount()
  })

  it.each([
    ['Unable to read repository usage.', en.repositoriesPage.usageReadFailed],
    ['Repository is not ready for metrics collection.', en.repositoriesPage.usageRepositoryNotReady],
    ['repo.status failed', en.repositoriesPage.usageProbeFailed],
  ])('localizes known usage failure: %s', (error, expected) => {
    const wrapper = mountSection({ status: 'failed', error: ` ${error} ` })
    expect(wrapper.get('.repository-usage-section__notice-reason').text()).toBe(expected)
    wrapper.unmount()
  })

  it('keeps unknown diagnostics and uses a localized fallback for empty errors', () => {
    const wrapper = mountSection({ status: 'failed', error: 'Custom diagnostic: upstream refused request' })
    expect(wrapper.get('.repository-usage-section__notice-reason').text()).toBe('Custom diagnostic: upstream refused request')
    wrapper.unmount()
    const empty = mountSection({ status: 'failed', error: ' ' })
    expect(empty.get('.repository-usage-section__notice-reason').text()).toBe(en.repositoriesPage.usageRefreshFailed)
    empty.unmount()
  })

  it.each([
    ['Request failed: password=private-password', 'private-password'],
    ['Request failed: access_key=private-access-key', 'private-access-key'],
    ['Request failed: Authorization: Bearer private-token', 'private-token'],
    ['Request failed: https://example.invalid/?secret=private-secret', 'private-secret'],
  ])('redacts sensitive values in unknown diagnostics: %s', (error, secret) => {
    const wrapper = mountSection({ status: 'failed', error })
    const reason = wrapper.get('.repository-usage-section__notice-reason').text()
    expect(reason).toContain('Request failed:')
    expect(reason).toContain('[REDACTED]')
    expect(wrapper.text()).not.toContain(secret)
    wrapper.unmount()
  })
})

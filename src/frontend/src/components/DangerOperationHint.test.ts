// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import DangerOperationHint from './DangerOperationHint.vue'

describe('DangerOperationHint', () => {
  it('renders an accessible lightweight explanation without actions or alert semantics', () => {
    const wrapper = mount(DangerOperationHint, { props: { text: 'Resolve bindings before deletion.' } })
    expect(wrapper.get('p').text()).toBe('Resolve bindings before deletion.')
    expect(wrapper.get('svg').attributes('aria-hidden')).toBe('true')
    expect(wrapper.find('[role="alert"]').exists()).toBe(false)
    expect(wrapper.find('button').exists()).toBe(false)
    wrapper.unmount()
  })
})

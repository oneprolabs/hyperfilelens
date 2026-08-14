// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import CopilotMarkdown from './CopilotMarkdown.vue'

describe('CopilotMarkdown', () => {
  it('renders safe links without allowing executable protocols', () => {
    const wrapper = mount(CopilotMarkdown, {
      props: {
        content: [
          '[Documentation](https://example.com/docs)',
          '[Unsafe](javascript:alert(1))',
          '<img src=x onerror=alert(1)>',
        ].join('\n\n'),
      },
    })

    const links = wrapper.findAll('a')
    expect(links).toHaveLength(1)
    expect(links[0].attributes()).toEqual(expect.objectContaining({
      href: 'https://example.com/docs',
      rel: 'noopener noreferrer',
      target: '_blank',
    }))
    expect(wrapper.html()).toContain('Unsafe')
    expect(wrapper.html()).not.toContain('javascript:')
    expect(wrapper.html()).toContain('&lt;img src=x onerror=alert(1)&gt;')
  })
})

// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { describe, expect, it } from 'vitest'
import NodeVersionCell from './NodeVersionCell.vue'
import type { ApiNode } from '../../types/node'

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      nodesPage: {
        versionUpgradeAvailable: 'New version',
        latestVersionTip: 'Latest version: {version}',
      },
    },
  },
})

const node: ApiNode = {
  id: 1,
  organization: 1,
  name: 'proxy-1',
  role: 'proxy',
  status: 'active',
  version: '0.2.3',
}

function mountCell(overrides: Record<string, unknown> = {}) {
  return mount(NodeVersionCell, {
    props: {
      node,
      versionLabel: '0.2.3',
      targetVersion: '0.2.10',
      updateAvailable: true,
      ...overrides,
    },
    global: {
      plugins: [i18n],
      stubs: {
        ElTooltip: {
          template: '<div class="tooltip-stub"><slot /></div>',
        },
      },
    },
  })
}

describe('NodeVersionCell', () => {
  it('shows an accessible update hint for a compatible newer release', () => {
    const wrapper = mountCell()
    expect(wrapper.text()).toContain('0.2.3')
    expect(wrapper.text()).toContain('New version')
    const hint = wrapper.get('.node-version-cell__hint')
    expect(hint.classes()).toContain('hfl-table-no-tooltip')
    expect(hint.attributes('aria-label')).toBe(
      'Latest version: 0.2.10',
    )
  })

  it.each([
    { updateAvailable: false },
    { targetVersion: null },
    { showUpdateHint: false },
  ])('hides the update hint for %o', (props) => {
    expect(mountCell(props).find('.node-version-cell__hint').exists()).toBe(false)
  })

  it('suppresses the hint while the server reports an active upgrade', () => {
    const upgradingNode: ApiNode = {
      ...node,
      lifecycle: {
        kind: 'upgrade',
        state: 'upgrading',
        target_version: '0.2.10',
      },
    }
    const wrapper = mountCell({ node: upgradingNode })

    expect(wrapper.text()).toContain('0.2.3')
    expect(wrapper.text()).toContain('0.2.10')
    expect(wrapper.find('.node-version-cell__hint').exists()).toBe(false)
  })

  it('suppresses the hint for a locally queued upgrade display', () => {
    const wrapper = mountCell({
      resolveVersionDisplay: () => ({
        upgrading: true,
        versionLabel: '0.2.3',
        targetVersion: '0.2.10',
      }),
    })

    expect(wrapper.find('.node-version-cell__hint').exists()).toBe(false)
  })
})

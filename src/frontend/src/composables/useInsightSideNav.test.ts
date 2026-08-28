import { describe, expect, it, vi } from 'vitest'

import { useInsightSideNav } from './useInsightSideNav'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

describe('useInsightSideNav', () => {
  it('keeps the temporarily hidden Usage page out of the sidebar', () => {
    const paths = useInsightSideNav().value
      .flatMap((group) => group.children || [])
      .map((item) => item.to)

    expect(paths).toContain('/insight/copilot')
    expect(paths).toContain('/insight/gateways')
    expect(paths).not.toContain('/insight/usage')
  })
})

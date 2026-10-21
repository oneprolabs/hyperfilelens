import { describe, expect, it } from 'vitest'
import { insightPermissionHints, insightRouteRedirect, resolveInsightEntry } from './insightAccess'

describe('Insights permission hints', () => {
  it.each([
    [[], '/'],
    [['configuration.roles.manage'], '/'],
    [['insights.ai_models.view'], '/'],
    [['insights.usage.view'], '/insight/usage'],
    [['insights.private_data_gateways.view'], '/insight/gateways'],
    [['insights.chats.view'], '/insight/copilot'],
    [['insights.usage.view', 'insights.chats.view'], '/insight/copilot'],
  ])('selects an accessible tenant page for %j', (permissions, expected) => {
    expect(resolveInsightEntry(insightPermissionHints({ permissions }, true))).toBe(expected)
  })

  it('accepts Group grants for Chat and Gateways but not organization-wide Usage', () => {
    const has = insightPermissionHints({
      group_permissions: ['insights.chats.view', 'insights.chats.use', 'insights.private_data_gateways.view'],
    }, true)
    expect(resolveInsightEntry(has)).toBe('/insight/copilot')
    expect(insightRouteRedirect('/insight/copilot/new', has)).toBeUndefined()
    expect(insightRouteRedirect('/insight/gateways', has)).toBeUndefined()
    expect(resolveInsightEntry(insightPermissionHints({ group_permissions: ['insights.usage.view'] }, true))).toBe('/')
  })

  it('does not conflate viewing Chats with creating them', () => {
    const has = insightPermissionHints({ permissions: ['insights.chats.view'] }, true)
    expect(insightRouteRedirect('/insight/copilot', has)).toBeUndefined()
    expect(insightRouteRedirect('/insight/copilot/new', has)).toBe('/insight/copilot')
    expect(insightRouteRedirect('/insight/copilot/new/', has)).toBe('/insight/copilot')
  })

  it.each(['/insight/copilot', '/insight/copilot/new', '/insight/usage', '/insight/gateways', '/INSIGHT/COPILOT/NEW/'])(
    'rejects direct access to %s without permissions', (path) => {
      expect(insightRouteRedirect(path, insightPermissionHints(undefined, true))).toBe('/')
    },
  )

  it('keeps a non-Chat Insights reader inside their accessible module', () => {
    const has = insightPermissionHints({ permissions: ['insights.usage.view'] }, true)
    expect(insightRouteRedirect('/insight/copilot/new', has)).toBe('/insight/usage')
    expect(insightRouteRedirect('/insight/gateways', has)).toBe('/insight/usage')
  })

  it('preserves the independent shared-Q&A contract and unrelated routes', () => {
    const has = insightPermissionHints(undefined, true)
    for (const path of ['/insight', '/insight/copilot/shared', '/insight/copilot/shared/', '/node/roles', '/platform-ops/engine/knowledge-base']) {
      expect(insightRouteRedirect(path, has)).toBeUndefined()
    }
  })

  it('preserves Community access without stored Role permissions', () => {
    const has = insightPermissionHints(undefined, false)
    expect(resolveInsightEntry(has)).toBe('/insight/copilot')
    for (const path of ['/insight/copilot/new', '/insight/gateways', '/insight/usage']) {
      expect(insightRouteRedirect(path, has)).toBeUndefined()
    }
  })
})

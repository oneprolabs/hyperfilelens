import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../../locales/en'
import { copilotGatewayKind } from '../../lib/copilotGatewayTerminology'
import { compactSourceText } from '../../test/sourceText'

const source = (path: string) => compactSourceText(readFileSync(resolve(process.cwd(), path), 'utf8'))

const newChat = source('src/pages/insight/NewCopilotChat.vue')
const contextBar = source('src/pages/insight/copilot/CopilotContextBar.vue')
const bindingSetup = source('src/pages/insight/copilot/CopilotBindingSetup.vue')
const nodeApi = source('src/lib/nodeApi.ts')

describe('Copilot Data Gateway product terminology', () => {
  it('uses Gateway scope as the authoritative persisted Chat type', () => {
    expect(copilotGatewayKind('platform', 'manual')).toBe('public')
    expect(copilotGatewayKind('user', 'auto')).toBe('private')
    expect(copilotGatewayKind(null, 'manual')).toBe('private')
    expect(copilotGatewayKind(null, 'auto')).toBe('public')
  })

  it('defines one centralized Public and Private vocabulary', () => {
    const copy = en.insight.copilot

    expect(copy.gatewayPublicTitle).toBe('Public Data Gateway')
    expect(copy.gatewayPrivateTitle).toBe('Private Data Gateway')
    expect(copy.gatewayTypePublic).toBe('Public')
    expect(copy.gatewayTypePrivate).toBe('Private')
    expect(copy.gatewayPublicDescription).toContain('your data remains isolated')
    expect(copy.gatewayPublicUnavailable).toBe(
      'No public Data Gateway is available. Select a private Data Gateway or contact your administrator.',
    )
  })

  it('uses translations instead of old user-facing Gateway labels', () => {
    for (const component of [newChat, contextBar, bindingSetup]) {
      expect(component).not.toMatch(/Platform Gateway|Private Gateway|platform gateway|private gateway/)
    }

    expect(newChat).toContain("t('insight.copilot.gatewayPublicTitle')")
    expect(newChat).toContain("t('insight.copilot.gatewayPrivateTitle')")
    expect(contextBar).toContain("t('insight.copilot.gatewayTypePublic')")
    expect(contextBar).toContain("t('insight.copilot.gatewayTypePrivate')")
    expect(contextBar).toContain('copilotGatewayKind(')
  })

  it('shows the selected Gateway name for both Chat detail types', () => {
    expect(contextBar).toContain("<dl><dt>{{ t('insight.copilot.gatewayNameLabel') }}</dt>")
    expect(contextBar).not.toContain('v-if="session.gateway_selection_mode === \'manual\'"')
  })

  it('localizes every Chat context status and fallback label', () => {
    for (const key of [
      'sessionRecovering',
      'sessionRecoveryAttention',
      'sessionPreparationFailed',
      'sessionPreparing',
      'sessionDeleting',
      'sessionAnswering',
      'sessionReady',
      'backupSourceFallback',
      'contextNoFilesSelected',
      'contextCreatedAt',
      'visualUnderstandingUnavailable',
    ]) {
      expect(contextBar).toContain(`t('insight.copilot.${key}'`)
    }
    expect(contextBar).toContain('new Intl.DateTimeFormat(locale.value')
  })

  it('uses Public terminology for Platform Ops service actions', () => {
    const copy = en.insight.dataGateway

    expect(copy.defaultActive).toBe('Public Default')
    expect(copy.defaultSet).toBe('Set as Public Default')
    expect(copy.defaultSetSuccess).toBe('Public Data Gateway default updated.')
    expect(copy.origin.platform).toBe('Platform')
    expect(en.platformOps.engineGateway.addSubtitle).toContain('Public Data Gateway')
    expect(nodeApi).not.toContain('Platform gateway enrollment response is incomplete')
  })
})

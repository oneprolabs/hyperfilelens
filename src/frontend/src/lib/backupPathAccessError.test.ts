import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../locales/en'
import { backupPathAccessSummaryKey } from './backupPathAccessError'
import { ERROR_CODE_FALLBACK_EN } from './errors/registry'
import { resolveErrorMessage } from './errors/resolver'

const catalogs = [
  en,
  ...['zh-hans', 'es'].map((id) => JSON.parse(readFileSync(
    resolve(process.cwd(), `../../language-packs/packs/${id}/frontend/messages.json`),
    'utf8',
  ))),
]

describe('backup path access guidance', () => {
  it.each([
    ['AGENT.PATH_OUTSIDE_USER_HOME', 'agentPathOutsideUserHome'],
    ['AGENT.PATH_READ_PERMISSION_DENIED', 'agentPathReadPermissionDenied'],
    ['AGENT.PATH_PERMISSION_DENIED', 'agentPathPermissionDenied'],
  ])('uses a short summary and matching detail for %s', (errorCode, key) => {
    expect(backupPathAccessSummaryKey(errorCode)).toBe(`errors.codes.${key}Short`)
    for (const catalog of catalogs) {
      const detail = catalog.errors.codes[key]
      const summary = catalog.errors.codes[`${key}Short`]
      expect(typeof summary).toBe('string')
      expect(summary.length).toBeLessThan(detail.length)
      expect(resolveErrorMessage(
        { status: 403, errorCode, message: 'raw diagnostic' },
        () => detail,
      )).toBe(detail)
      expect(detail).not.toMatch(/Host files|sudo/)
      expect(detail).not.toContain(catalog.nodeLifecycle.installationModeSystem)
    }
    expect(resolveErrorMessage({ status: 403, errorCode, message: 'raw diagnostic' })).toBe(en.errors.codes[key as keyof typeof en.errors.codes])
    expect(ERROR_CODE_FALLBACK_EN[errorCode]).toBe(en.errors.codes[key as keyof typeof en.errors.codes])
  })

  it('reserves root reinstallation guidance for an explicit Home boundary', () => {
    for (const catalog of catalogs) {
      const codes = catalog.errors.codes
      expect(codes.agentPathOutsideUserHome).toContain('root')
      expect(codes.agentPathOutsideUserHome).toContain('\n')
      expect(codes.agentPathOutsideUserHome).not.toContain('\n\n')
      expect(codes.agentPathReadPermissionDenied).not.toContain('root')
      expect(codes.agentPathPermissionDenied).not.toContain('root')
    }
  })

  it('does not infer scope from unrelated errors or legacy diagnostic text', () => {
    for (const code of [
      'AGENT.PATH_VALIDATE_FAILED',
      'AGENT.PATH_PROTECTED',
      'AGENT.TIMEOUT',
      'AGENT.UNREACHABLE',
      'permission denied',
      '',
    ]) {
      expect(backupPathAccessSummaryKey(code)).toBeUndefined()
    }
  })

  it('keeps the full error inline, with a short table summary and shared hover detail', () => {
    const wizard = readFileSync(
      resolve(process.cwd(), 'src/pages/protection/BackupCreateWizard.vue'), 'utf8',
    )
    expect(wizard).toContain('createSourceDirIssueSummary(row.id)')
    expect(wizard).toContain('class="create-manual-path__error-details"')
    expect(wizard).toContain('class="create-source-dir-preview-empty__reason hfl-table-no-tooltip"')
    expect(wizard).toMatch(/\.create-manual-path__error \{[^}]*white-space: pre-line;/)
    expect(wizard).toContain('@input="clearManualSourcePathError(row.id)"')
    expect(wizard).toContain('delete manualSourcePathErrorCodeBySource[sourceId]')
    expect(wizard).toContain('delete manualSourcePathErrorBySource[sourceId]')
    expect(wizard).toContain('if (!backupPathAccessSummaryKey(code)) ElMessage.error')
    expect(wizard).toMatch(/setManualSourcePathError\(sourceId, message, code\)[\s\S]*?return[\s\S]*?const resolvedPath = pathInfo.path/)
  })
})

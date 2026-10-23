import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from '../../locales/en'

function source(file: string): string {
  return readFileSync(resolve(process.cwd(), file), 'utf8')
}

describe('Admin Engine detail drawers', () => {
  const assistants = source('src/pages/insight/InsightAssistantDetailDrawer.vue')
  const skills = source('src/pages/insight/InsightSkillDetailDrawer.vue')
  const mcp = source('src/pages/insight/InsightMcpServerDetailDrawer.vue')

  it('structures Assistants like Knowledge Source Basic Information', () => {
    expect(en.insight.assistants.sectionGeneral).toBe('General')
    expect(en.insight.assistants.sectionModels).toBe('Models')
    expect(en.insight.assistants.sectionRetrieval).toBe('Retrieval')
    expect(en.insight.assistants.sectionTechnical).toBe('Technical Details')
    expect(assistants).toContain("t('insight.assistants.sectionGeneral')")
    expect(assistants).toContain("t('insight.assistants.sectionModels')")
    expect(assistants).toContain("t('insight.assistants.sectionKnowledgeSource')")
    expect(assistants).toContain("t('insight.assistants.sectionRetrieval')")
    expect(en.insight.assistants.sectionTools).toBe('Skills & Workspace')
    expect(en.insight.assistants.fieldKnowledgeSourceStatus).toBe('Source Status')
    expect(en.insight.assistants.fieldWorkspaceContext).toBe('Workspace Context')
    expect(assistants).toContain("t('insight.assistants.sectionTools')")
    expect(assistants).toContain("t('insight.assistants.sectionTechnical')")
    expect(assistants).toContain("t('insight.assistants.fieldKnowledgeSourceStatus')")
    expect(assistants).toContain("t('insight.assistants.visibilityOrganization')")
    expect(assistants).toContain('organizationId')
    expect(assistants).toContain('`/platform-ops/orgs/${organizationId}`')
    expect(assistants).not.toContain('HflDetailDrawerFooter')
    expect(assistants).not.toContain('insight-detail-row-hint')
    expect(assistants).not.toContain('sectionVisibility')
    expect(assistants).not.toContain('colLearnStatus')
    expect(assistants).not.toContain('visibilityOrganizationShort')
  })

  it('structures Skills with General / Content / Technical and Status label', () => {
    expect(en.insight.skills.sectionGeneral).toBe('General')
    expect(en.insight.skills.sectionTechnical).toBe('Technical Details')
    expect(skills).toContain("t('insight.skills.sectionGeneral')")
    expect(skills).toContain("t('insight.skills.sectionContent')")
    expect(skills).toContain("t('insight.skills.sectionTechnical')")
    expect(skills).toContain("t('insight.skills.colStatus')")
    expect(skills).toContain("t('insight.skills.fieldSlug')")
    expect(skills).not.toContain('HflDetailDrawerFooter')
    expect(skills).not.toContain("t('insight.skills.fieldEnabled')")
    expect(skills).not.toContain("t('insight.skills.colContent')")
    expect(skills).not.toContain('create-filter-rules-preview')
    expect(skills).toContain('insight-skill-detail-content')
  })

  it('structures MCP with General / Runtime Settings aligned to list columns', () => {
    expect(en.insight.mcpServers.sectionGeneral).toBe('General')
    expect(en.insight.mcpServers.sectionConfig).toBe('Runtime Settings')
    expect(mcp).toContain("t('insight.mcpServers.sectionGeneral')")
    expect(mcp).toContain("t('insight.mcpServers.sectionConfig')")
    expect(mcp).toContain("t('insight.mcpServers.colStatus')")
    expect(mcp).toContain("t('insight.mcpServers.colTransport')")
    expect(mcp).toContain("t('insight.mcpServers.colEndpoint')")
    expect(mcp).not.toContain('HflDetailDrawerFooter')
    expect(mcp).not.toContain("t('insight.mcpServers.fieldEnabled')")
    expect(mcp).not.toContain('create-filter-rules-preview')
    expect(mcp).not.toContain("t('insight.mcpServers.colRuntimeSettings')")
    expect(mcp).not.toContain("t('insight.mcpServers.runtimeSettingsNone')")
    expect(mcp).toContain('v-if="configEntries.length"')
  })
})

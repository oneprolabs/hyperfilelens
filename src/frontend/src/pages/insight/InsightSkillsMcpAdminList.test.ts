import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

function source(file: string): string {
  return readFileSync(resolve(process.cwd(), file), 'utf8')
}

describe('Admin Skills / MCP Servers lists', () => {
  const skills = source('src/pages/insight/InsightSkills.vue')
  const mcp = source('src/pages/insight/InsightMcpServers.vue')

  it('paginates Skills like other Admin Engine lists', () => {
    expect(skills).toContain('PlatformOpsPagination')
    expect(skills).toContain('visibleRows')
  })

  it('keeps Skills list to Name and Enabled without Content column', () => {
    expect(skills).toContain("t('insight.skills.colName')")
    expect(skills).toContain("t('insight.skills.colEnabled')")
    expect(skills).not.toContain("t('insight.skills.colContent')")
  })

  it('confirms platform Skill deletes with assistant impact warning', () => {
    expect(skills).toContain('deleteConfirmPlatform')
  })

  it('paginates MCP Servers like other Admin Engine lists', () => {
    expect(mcp).toContain('PlatformOpsPagination')
    expect(mcp).toContain('visibleRows')
  })

  it('confirms platform MCP deletes with assistant impact warning', () => {
    expect(mcp).toContain('deleteConfirmPlatform')
  })
})

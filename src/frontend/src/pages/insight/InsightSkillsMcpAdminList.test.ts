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

  it('keeps Skills list to Name and Status without Content column', () => {
    expect(skills).toContain("t('insight.skills.colName')")
    expect(skills).toContain("t('insight.skills.colStatus')")
    expect(skills).not.toContain("t('insight.skills.colEnabled')")
    expect(skills).not.toContain("t('insight.skills.colContent')")
  })

  it('keeps MCP list to Name, Transport, Endpoint, and Status', () => {
    expect(mcp).toContain("t('insight.mcpServers.colName')")
    expect(mcp).toContain("t('insight.mcpServers.colTransport')")
    expect(mcp).toContain("t('insight.mcpServers.colEndpoint')")
    expect(mcp).toContain("t('insight.mcpServers.colStatus')")
    expect(mcp).not.toContain("t('insight.mcpServers.colEnabled')")
    expect(mcp).not.toContain("t('insight.mcpServers.colRuntimeSettings')")
  })

  it('confirms Skill enable, disable, and delete with Users-style DangerConfirmDialog', () => {
    expect(skills).toContain("requestLifecycleAction('enable'")
    expect(skills).toContain("requestLifecycleAction('disable'")
    expect(skills).toContain("requestLifecycleAction('delete'")
    expect(skills).toContain("confirm-mode=\"lifecycleAction === 'delete' ? 'keyword' : 'single'\"")
    expect(skills).toContain('enableConfirmMessage')
    expect(skills).toContain('disableConfirmWarning')
    expect(skills).toContain('deleteConfirmWarning')
  })

  it('paginates MCP Servers like other Admin Engine lists', () => {
    expect(mcp).toContain('PlatformOpsPagination')
    expect(mcp).toContain('visibleRows')
  })

  it('confirms MCP enable, disable, and delete with Users-style DangerConfirmDialog', () => {
    expect(mcp).toContain("requestLifecycleAction('enable'")
    expect(mcp).toContain("requestLifecycleAction('disable'")
    expect(mcp).toContain("requestLifecycleAction('delete'")
    expect(mcp).toContain("confirm-mode=\"lifecycleAction === 'delete' ? 'keyword' : 'single'\"")
    expect(mcp).toContain('enableConfirmMessage')
    expect(mcp).toContain('disableConfirmWarning')
    expect(mcp).toContain('deleteConfirmWarning')
  })
})

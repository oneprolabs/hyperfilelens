import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { describe, expect, it } from 'vitest'

const nodesPage = readFileSync(fileURLToPath(new URL('./Nodes.vue', import.meta.url)), 'utf8')
const basicInfo = readFileSync(
  fileURLToPath(new URL('../../components/NodeBasicInfoPanel.vue', import.meta.url)),
  'utf8',
)
const addProxyFs = readFileSync(
  fileURLToPath(new URL('./AddProxyFsRepository.vue', import.meta.url)),
  'utf8',
)
const editProxyFs = readFileSync(
  fileURLToPath(new URL('./EditProxyFsRepo.vue', import.meta.url)),
  'utf8',
)
const editNas = readFileSync(
  fileURLToPath(new URL('./RepairNasRepository.vue', import.meta.url)),
  'utf8',
)

describe('Proxy Repository Server Address UI', () => {
  it('edits the optional Proxy-level override and explains the automatic Host IP default', () => {
    expect(nodesPage).toContain("node.repository_server_address || ''")
    expect(nodesPage).toContain('repository_server_address: repositoryServerAddressInput.value.trim()')
    expect(nodesPage).toContain("t('nodesPage.repositoryServerAddressAutoPlaceholder'")
    expect(nodesPage).toContain("t('nodesPage.repositoryServerAddressHint')")
    expect(nodesPage).toContain('class="source-action-dialog__form proxy-host-edit-form"')
    expect(nodesPage).toContain('width="min(520px, calc(100vw - 32px))"')
    expect(nodesPage).toContain('gap: 20px')
    expect(nodesPage).toContain('class="proxy-host-edit-form__hint"')
  })

  it('shows the effective address and Auto or Custom source beside Host IP', () => {
    const hostIp = basicInfo.indexOf("t('protection.sourceResources.colHostIp')")
    const repositoryAddress = basicInfo.indexOf("t('nodesPage.repositoryServerAddress')")

    expect(hostIp).toBeGreaterThan(-1)
    expect(repositoryAddress).toBeGreaterThan(hostIp)
    expect(basicInfo).toContain('effective_repository_server_address')
    expect(basicInfo).toContain("repository_server_address_source === 'proxy_override'")
  })

  it('does not expose the legacy Repository-level address in create or edit forms', () => {
    expect(addProxyFs).not.toContain('proxy_repository_server_host')
    expect(editProxyFs).not.toContain('proxy_repository_server_host')
    expect(editNas).not.toContain('proxy_repository_server_host')
  })
})

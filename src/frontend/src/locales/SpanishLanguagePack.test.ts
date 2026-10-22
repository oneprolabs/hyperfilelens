import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { en } from './en'

const packRoot = resolve(process.cwd(), '../../language-packs/packs/es')
const catalog = JSON.parse(
  readFileSync(resolve(process.cwd(), '../../language-packs/catalog.json'), 'utf8'),
) as { packs: Array<{ id: string, path: string, status: string }> }
const definition = JSON.parse(
  readFileSync(resolve(packRoot, 'definition.json'), 'utf8'),
) as Record<string, unknown>
const spanish = JSON.parse(
  readFileSync(resolve(packRoot, 'frontend/messages.json'), 'utf8'),
) as typeof en
const allowedEnglish = JSON.parse(
  readFileSync(resolve(packRoot, 'frontend/allowed-english.json'), 'utf8'),
) as string[]

function flatten(value: Record<string, unknown>, prefix = ''): Map<string, unknown> {
  const result = new Map<string, unknown>()
  for (const [key, child] of Object.entries(value)) {
    const path = prefix ? `${prefix}.${key}` : key
    if (child && typeof child === 'object' && !Array.isArray(child)) {
      for (const [nestedKey, nestedValue] of flatten(child as Record<string, unknown>, path)) {
        result.set(nestedKey, nestedValue)
      }
    } else {
      result.set(path, child)
    }
  }
  return result
}

describe('Spanish language pack', () => {
  it('is bundled with standard regional aliases', () => {
    expect(catalog.packs).toContainEqual({ id: 'es', path: 'packs/es', status: 'bundled' })
    expect(definition).toMatchObject({
      id: 'es',
      display_name: 'Español',
      frontend_code: 'es',
      backend_code: 'es',
      component_locale: 'es',
      aliases: ['es-es', 'es-mx', 'es-ar', 'es-cl', 'es-co', 'es-pe'],
    })
  })

  it('covers the complete English message contract', () => {
    // Flat overrides and nested translations have no meaningful key ordering.
    expect([...flatten(spanish).keys()].sort()).toEqual([...flatten(en).keys()].sort())
  })

  it('ships reviewed labels for authentication, protection, and Insight', () => {
    expect(spanish.login.welcomeTitle).toBe('Bienvenido a HyperFileLens')
    expect(spanish.login.emailCodeMethod).toBe('Código de verificación')
    expect(spanish.login.forgotPwd).toBe('¿Olvidó su contraseña?')
    expect(spanish.login.noAccount).toBe('¿No tiene una cuenta?')
    expect(spanish.account.menuSignOut).toBe('Cerrar sesión')
    expect(spanish.protection.side.backupPolicies).toBe('Políticas de copia de seguridad')
    expect(spanish.protection.sourceResources.colDiskCount).toBe('Discos')
    expect(spanish.insight.copilot.sessionReady).toBe('Listo')
    expect(spanish.insight.copilot.contextNoFilesSelected).toBe('No hay archivos seleccionados')
    expect(spanish.insight.copilot.contextCreatedAt).toBe('Creado: {time}')
    expect(spanish.insight.usage.usageByBackupSource).toBe(
      'Uso por origen de copia de seguridad',
    )
    expect(spanish.protection.taskProgress.backup.preparing).toBe(
      'Preparando la copia de seguridad...',
    )
    expect(spanish.protection.backupsPage.btnAddBackupSource).toBe('Añadir origen')
    expect(spanish.protection.backupsPage.btnDeleteBackups).toBe(
      'Eliminar copias de seguridad',
    )
    expect(spanish.protection.backupsPage.colBackupSource).toBe(
      'Origen de copia de seguridad',
    )
    expect(spanish.protection.backupsPage.targetDetailS3Bucket).toBe('Bucket')
    expect(spanish.protection.backupsPage.msgPickDirsForBatchTarget).toBe(
      'Seleccione primero los directorios de copia de seguridad que deben usar el mismo repositorio de destino.',
    )
    expect(spanish.protection.backupsPage.summaryAbnormalTarget).toBe(
      'Destinos con problemas',
    )
    expect(spanish.protection.policiesPage.confirmDisablePoliciesTitle).toBe(
      'Desactivar políticas de copia de seguridad',
    )
    expect(spanish.protection.policiesPage.batchDisableSkipNotice).toContain(
      'se omitirán {skipped} que ya están desactivados',
    )
    expect(spanish.ops.task.status.failedTimedOut).toBe(
      'Fallida / Tiempo de espera agotado',
    )
    expect(spanish.insight.skills.fieldEnabledHint).toContain(
      'Las habilidades desactivadas',
    )
    expect(spanish.insight.dataGateway.deleteForceTitle).toBe(
      'Eliminación forzada de la puerta de enlace de datos',
    )
    expect(spanish.protection.side.sourceAgents).toBe('Hosts proxy')
    expect(spanish.insight.side.copilot).toBe('Copiloto de IA')
    expect(spanish.insight.side.dataGateway).toBe('Puertas de enlace de datos')
    expect(spanish.platformOps.nav.engineGateways).toBe('Puertas de enlace de datos')
    expect(spanish.insight.kb.sourceTypeGatewayLocalDemoHint).toContain(
      'es solo una vista previa',
    )
    expect(spanish.repositoriesPage.waitingForFirstCheck).toBe(
      'Esperando la primera comprobación',
    )
    expect(spanish.platformOps.quotaUsage.colRemaining).toBe('Restante')
    expect(spanish.platformOps.nav.groupMonitoring).toBe('Supervisión')
    expect(spanish.platformOps.nav.groupAlerting).toBe('Alertas')
    expect(spanish.platformOps.platform.environmentTitle).toBe('Entorno')
    expect(spanish.platformOps.settings.turnstile.intro).toContain(
      'credenciales de Cloudflare Turnstile',
    )
    expect(spanish.insight.copilot.visualUnderstandingUnavailable).toContain(
      'La comprensión visual no está disponible',
    )
  })

  it('uses consistent SMB share terminology and usable technical examples', () => {
    const shareName = 'Nombre del recurso compartido'
    expect(spanish.protection.sourceResources.colNasShareName).toBe(shareName)
    expect(spanish.repositoriesPage.colNasShareName).toBe(shareName)
    expect(spanish.addNasRepo.fieldSmbShare).toBe(shareName)
    expect(spanish.repairNasRepo.labelShareName).toBe(shareName)
    expect(spanish.addNasRepo.phSmbShare).toBe('data')
    expect(spanish.protection.sourceResources.nasPhSmbShare).toBe('data')
    expect(spanish.protection.sourceResources.nasPhSmbUsername).toBe('admin')
    expect(spanish.protection.sourceResources.nasPhMountOptionsSmb)
      .toBe('vers=3.0,iocharset=utf8,uid=1000,gid=1000')
    expect(spanish.nodesDeploy.roleProxyDesc).toContain('recursos compartidos NAS')
    expect(spanish.nodesDeploy.proxyIntroDesc).toContain('recursos compartidos NAS')
  })

  it('contains no migration markers or unrelated writing systems', () => {
    const serialized = JSON.stringify(spanish)
    const containsUnrelatedWritingSystem = Array.from(serialized).some((character) => {
      const codePoint = character.codePointAt(0) ?? 0
      const isCjk = codePoint >= 0x3400 && codePoint <= 0x9fff
      const isCyrillic = codePoint >= 0x0400 && codePoint <= 0x04ff
      return isCjk || isCyrillic
    })
    expect(serialized).not.toMatch(/HFLPROTECTED|ZXQ|QXZ|[⟦⟧]/)
    expect(containsUnrelatedWritingSystem).toBe(false)
    expect(allowedEnglish).toContain('insight.aiSettings.topP')
    expect(allowedEnglish).toContain('repositoriesPage.cleanupResourceSeparator')
    expect(spanish.repositoriesPage.cleanupResourceSeparator).toBe(en.repositoriesPage.cleanupResourceSeparator)
    // These are also valid Spanish terms, standard identifiers or product
    // module names, not untranslated descriptions.
    const sharedGovernanceTerms = [
      'platformOps.orgGovernance.general',
      'platformOps.orgGovernance.roles',
      'settings.groupsPage.general',
      'settings.membersPage.general',
      'settings.resourcesPage.general',
      'settings.rolesPage.general',
      'settings.rolesPage.groups.insights',
      'settings.rolesPage.names.auditor',
      'settings.rolesPage.resourceLabels.configuration_roles',
      'settings.rolesPage.resourceLabels.insights_chats',
      'platformOps.storageProviders.general',
      'platformOps.storageProviders.id',
    ]
    expect(allowedEnglish.filter(key => key !== 'repositoriesPage.cleanupResourceSeparator').length).toBeLessThanOrEqual(327 + sharedGovernanceTerms.length)
    const englishFlat = flatten(en)
    const spanishFlat = flatten(spanish)
    for (const key of sharedGovernanceTerms) {
      expect(allowedEnglish).toContain(key)
      expect(spanishFlat.get(key)).toBe(englishFlat.get(key))
    }
    expect(serialized).not.toMatch(
      /Failed to|\bDefault\b|\bRefresh\b|Disfraces|&quot;|&amp;/,
    )
    expect(serialized).not.toMatch(
      /Salvar|ahorrad[ao]|Copia fallado|Cargo cancelado|directorios sombríos|Lista de referencia|Sendero|Supir|Desregistration|No es una farsa|Preparando refuerzos|\bGastos\b/,
    )
    expect(serialized).not.toMatch(
      /\brespaldo(?:s)?\b|Cubeta|Punto final|a granel|Suprímase|discapacitad|Ataque los|Metas anormales|Permaneciendo|No corres|Medio ambiente|Limpieza frustrada|Credenciales voluminosas|configs loaded|Final Source|Removing Agent/i,
    )
    expect(serialized).not.toMatch(
      /acciones NAS|Nombre compartido|acceso leído a la parte|monta la parte|ruta de compartir|ruta compartida|parte subyacente/i,
    )
    expect(allowedEnglish).not.toContain('insight.copilot.contextNoFilesSelected')
  })
})

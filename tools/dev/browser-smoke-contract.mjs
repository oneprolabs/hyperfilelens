const COMMUNITY_PLATFORM_ROUTES = Object.freeze([
  '/platform-ops/engine/ai-settings',
  '/platform-ops/platform/runtime-environment',
])

const ENTERPRISE_PLATFORM_ROUTES = Object.freeze([
  '/platform-ops/monitoring/monitor',
  '/platform-ops/orgs',
])

const CONTRACTS = Object.freeze({
  community: Object.freeze({
    edition: 'community',
    platformRoutes: COMMUNITY_PLATFORM_ROUTES,
    mobilePlatformStartPath: '/platform-ops/engine/ai-settings',
    mobilePlatformTargetPath: '/platform-ops/platform/runtime-environment',
    verifyPlatformPrimaryAction: false,
  }),
  enterprise: Object.freeze({
    edition: 'enterprise',
    platformRoutes: ENTERPRISE_PLATFORM_ROUTES,
    mobilePlatformStartPath: '/platform-ops/monitoring/monitor',
    mobilePlatformTargetPath: '/platform-ops/orgs',
    verifyPlatformPrimaryAction: true,
  }),
})

export function resolveSmokeContract(rawEdition = 'community') {
  const edition = String(rawEdition || '').trim().toLowerCase()
  const contract = CONTRACTS[edition]
  if (!contract) {
    throw new Error(`Unsupported release edition for browser smoke: ${rawEdition}`)
  }
  return contract
}

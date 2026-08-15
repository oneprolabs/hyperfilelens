import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveSmokeContract } from '../dev/browser-smoke-contract.mjs'

test('Community smoke contract only covers Host platform routes', () => {
  const contract = resolveSmokeContract('community')

  assert.deepEqual(contract.platformRoutes, [
    '/platform-ops/engine/ai-settings',
    '/platform-ops/platform/runtime-environment',
  ])
  assert.equal(contract.mobilePlatformStartPath, '/platform-ops/engine/ai-settings')
  assert.equal(contract.mobilePlatformTargetPath, '/platform-ops/platform/runtime-environment')
  assert.equal(contract.verifyPlatformPrimaryAction, false)
  assert.equal(contract.platformRoutes.includes('/platform-ops/orgs'), false)
})

test('Enterprise smoke contract covers platform governance routes', () => {
  const contract = resolveSmokeContract('enterprise')

  assert.deepEqual(contract.platformRoutes, [
    '/platform-ops/monitoring/monitor',
    '/platform-ops/orgs',
  ])
  assert.equal(contract.mobilePlatformStartPath, '/platform-ops/monitoring/monitor')
  assert.equal(contract.mobilePlatformTargetPath, '/platform-ops/orgs')
  assert.equal(contract.verifyPlatformPrimaryAction, true)
})

test('Smoke contract rejects unknown editions', () => {
  assert.throws(
    () => resolveSmokeContract('business'),
    /Unsupported release edition for browser smoke/,
  )
})

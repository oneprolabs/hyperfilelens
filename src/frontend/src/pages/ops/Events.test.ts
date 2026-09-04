import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const page = readFileSync(resolve(process.cwd(), 'src/pages/ops/Events.vue'), 'utf8')
const router = readFileSync(resolve(process.cwd(), 'src/router/index.ts'), 'utf8')

describe('Events page contract', () => {
  it('uses the durable events API and the shared Operations list layout', () => {
    expect(page).toContain('/api/v1/monitors/events/')
    expect(page).toContain('hfl-ops-stats-grid--4')
    expect(page).toContain('<HflTablePanel fill>')
    expect(page).not.toContain('/api/v1/monitors/attention/')
    expect(page).toContain('loadRequestSequence')
  })

  it('keeps the old health route as a compatibility redirect', () => {
    expect(router).toContain("{ path: 'ops/events', component: OpsEventsPage }")
    expect(router).toContain("{ path: 'ops/health', redirect: '/ops/events' }")
  })
})

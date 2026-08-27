import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

const primaryNavSource = readFileSync(
  resolve(process.cwd(), 'src/composables/useAppPrimaryNav.ts'),
  'utf8',
)
const topNavSource = readFileSync(resolve(process.cwd(), 'src/app/layout/TopNav.vue'), 'utf8')
const mobileNavSource = readFileSync(
  resolve(process.cwd(), 'src/components/MobileNavigationDrawer.vue'),
  'utf8',
)

describe('primary navigation icons', () => {
  it('defines one shared Lucide icon for every primary destination', () => {
    expect(primaryNavSource).toContain("{ to: '/', label: t('nav.overview'), icon: LayoutDashboard }")
    expect(primaryNavSource).toContain(
      "{ to: '/protection', label: t('nav.protection'), icon: ShieldCheck }",
    )
    expect(primaryNavSource).toContain(
      "{ to: '/insight', label: t('nav.insight'), icon: ChartNoAxesCombined }",
    )
    expect(primaryNavSource).toContain("{ to: '/node', label: t('nav.node'), icon: Settings }")
    expect(primaryNavSource).toContain("{ to: '/ops', label: t('nav.ops'), icon: Activity }")
  })

  it('renders decorative icons alongside labels in desktop and mobile navigation', () => {
    expect(topNavSource).toMatch(
      /:is="item\.icon"[\s\S]*?class="nav-item__icon"[\s\S]*?:size="16"[\s\S]*?aria-hidden="true"/,
    )
    expect(topNavSource).toContain('<span>{{ item.label }}</span>')

    expect(mobileNavSource).toMatch(
      /:is="item\.icon"[\s\S]*?:size="17"[\s\S]*?:stroke-width="2"[\s\S]*?aria-hidden="true"/,
    )
    expect(mobileNavSource).toContain('<span>{{ item.label }}</span>')
  })

  it('compacts icons and timezone text at constrained desktop widths', () => {
    expect(topNavSource).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1439\.98px\)[\s\S]*?\.nav-item__icon\s*{[\s\S]*?display:\s*none/,
    )
    expect(topNavSource).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1439\.98px\)[\s\S]*?\.timezone-display__label\s*{[\s\S]*?display:\s*none/,
    )
    expect(topNavSource).toContain('class="timezone-display__label"')
    expect(topNavSource).toMatch(
      /@media \(max-width: 1023\.98px\)[\s\S]*?\.nav-menu,[\s\S]*?display:\s*none/,
    )
  })

  it('keeps the desktop header flex chain shrinkable at laptop widths', () => {
    expect(topNavSource).toMatch(
      /\.top-nav\s*{[\s\S]*?width:\s*100%[\s\S]*?min-width:\s*0/,
    )
    expect(topNavSource).toMatch(
      /\.nav-menu\s*{[\s\S]*?min-width:\s*0[\s\S]*?flex:\s*0 1 auto/,
    )
    expect(topNavSource).toMatch(
      /\.right-menu\s*{[\s\S]*?min-width:\s*0[\s\S]*?flex:\s*0 1 auto/,
    )
  })

  it('keeps the timezone label and offset on a single line', () => {
    expect(topNavSource).toMatch(
      /\.timezone-display\s*\{[\s\S]*?white-space:\s*nowrap/,
    )
    expect(topNavSource).toMatch(
      /\.timezone-display\s*\{[\s\S]*?gap:\s*4px/,
    )
  })

  it('uses a compact desktop layout before switching to the mobile drawer', () => {
    expect(topNavSource).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1439\.98px\)[\s\S]*?\.nav-item\s*{[\s\S]*?min-width:\s*0[\s\S]*?padding-right:\s*8px/,
    )
    expect(topNavSource).toMatch(
      /@media \(min-width: 1024px\) and \(max-width: 1439\.98px\)[\s\S]*?\.top-nav \.platform-ops-entry\s*{[\s\S]*?border:\s*0[\s\S]*?background:\s*transparent/,
    )
    expect(topNavSource).toMatch(
      /\.top-nav \.platform-ops-entry svg\s*{[\s\S]*?width:\s*18px[\s\S]*?height:\s*18px/,
    )
    expect(topNavSource).toMatch(
      /\.top-nav \.platform-ops-entry:hover,[\s\S]*?background:\s*var\(--icon-btn-hover-bg,[\s\S]*?color:\s*var\(--icon-btn-hover-color/,
    )
    expect(topNavSource).toContain(':aria-label="t(\'nav.platformOps\')"')
  })
})

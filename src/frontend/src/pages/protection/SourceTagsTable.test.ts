import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'

describe('tag management table', () => {
  const content = readFileSync(resolve(process.cwd(), 'src/components/SourceTagActionContent.vue'), 'utf8')

  it('uses a quiet immediate-save note only in the wizard', () => {
    const wizard = readFileSync(resolve(process.cwd(), 'src/pages/protection/DataProtection.vue'), 'utf8')
    const sources = readFileSync(resolve(process.cwd(), 'src/pages/protection/BackupSources.vue'), 'utf8')
    const wizardDialog = wizard.split('v-model="wizardTagActionOpen"')[1]?.split('</el-dialog>')[0] || ''
    const sourcesDialog = sources.split('v-model="tagActionOpen"')[1]?.split('</el-dialog>')[0] || ''
    expect(wizardDialog).toContain('        wizard')
    expect(sourcesDialog).not.toContain('        wizard')
    expect(content).toContain('v-if="wizard"')
    expect(content).toContain("t('protection.tags.wizardSaveHint')")
    expect(content).not.toContain('el-alert')
    expect(content).toContain('to="/protection/source-tags"')
    expect(content).toContain('target="_blank"')
    expect(content).toContain('rel="noopener noreferrer"')
  })

  it.each([
    ['BackupSources.vue', 'tagActionOpen', 'tagActionIds', 'tagAction'],
    ['DataProtection.vue', 'wizardTagActionOpen', 'wizardTagIds', 'wizardTagAction'],
  ])('shares tag selection content and explicit action buttons in %s', (file, dialogModel, selectionModel, action) => {
    const page = readFileSync(resolve(process.cwd(), `src/pages/protection/${file}`), 'utf8')
    const dialog = page.split(`v-model="${dialogModel}"`)[1]?.split('</el-dialog>')[0] || ''
    expect(dialog).toContain('<SourceTagActionContent')
    expect(dialog).toContain(`v-model:ids="${selectionModel}"`)
    expect(dialog).toContain(`t(${action} === 'add' ? 'protection.tags.bind' : 'protection.tags.unbind')`)
    expect(dialog).not.toContain("t('common.confirm')")
    expect(page).toContain('class="hfl-table-no-tooltip source-tag-list"')
  })

  it('stacks full tag names and descriptions rather than clipping them side by side', () => {
    const styles = readFileSync(resolve(process.cwd(), 'src/styles/source-tag-ui.css'), 'utf8')
    expect(content).toContain('class="source-tag-picker__content"')
    expect(content).toContain('class="source-tag-picker__description"')
    expect(content).toContain('v-model="search"')
    expect(content).toContain(':show-icon="false"')
    expect(styles).toContain('.source-tag-picker__content .source-tag-badge__label')
    expect(styles).toContain('white-space: normal;')
    expect(styles).toContain('max-height: 264px;')
  })

  it('localizes compact impact summaries and secondary guidance', () => {
    for (const path of [
      '../../language-packs/packs/zh-hans/frontend/messages.json',
      '../../language-packs/packs/es/frontend/messages.json',
    ]) {
      const messages = JSON.parse(readFileSync(resolve(process.cwd(), path), 'utf8'))
      for (const key of ['bindSummary', 'unbindSummary', 'wizardSaveHint', 'unbindPreservesHint', 'manageTags', 'searchTags', 'viewSelectedSources', 'hideSelectedSources']) {
        expect(messages.protection.tags[key]).toBeTruthy()
      }
    }
    expect(content).toContain('associations: props.affected')
    expect(content).toContain(`v-if="operation === 'remove'"`)
  })

  it('shows creation time without duplicating actions in every row', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    expect(page).toContain("t('protection.tags.createdAt')")
    expect(page).toContain('formatAppDateTime(row.created_at)')
    expect(page).not.toContain(":label=\"t('protection.tags.actions')\"")
  })

  it('uses the shared dangerous-operation dialog for tag deletion', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    expect(page).toContain('v-model="deleteConfirmOpen"')
    expect(page).toContain('@confirm="deleteTarget && remove(deleteTarget, true)"')
    expect(page).not.toContain('ElMessageBox.confirm')
  })

  it('uses non-interactive badges when the description has its own column', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    const listStyles = readFileSync(resolve(process.cwd(), 'src/styles/list-page-ui.css'), 'utf8')
    const badgeStyles = readFileSync(resolve(process.cwd(), 'src/styles/source-tag-ui.css'), 'utf8')
    expect(page).toContain('<SourceTagBadge :tag="row" :interactive="false" :show-name-title="false" :show-icon="false" />')
    expect(page).toContain("t('protection.tags.description')")
    expect(page).toContain("row.description || '—'")
    expect(listStyles).toContain('> .cell > span:not(.el-tag)')
    expect(listStyles).toContain(':not(.source-tag-badge) {')
    expect(badgeStyles).toMatch(/\.source-tag-badge\s*\{\s*display:\s*inline-flex;/)
  })

  it('opens a read-only usage drawer from a nonzero source count', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    expect(page).toContain('v-if="row.source_count > 0"')
    expect(page).toContain('@click.stop="openUsage(row)"')
    expect(page).toContain('v-model="usageOpen"')
    expect(page).toContain('await listSourcesForTag(usageTag.value.id,')
    expect(page).toContain(':total="usageCount"')
  })

  it('uses the shared list toolbar utility and right-aligned pagination', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    expect(page).toContain('<HflTablePanel fill>')
    expect(page).toContain('<template #toolbar>')
    expect(page).toContain('<template #toolbar-utility>')
    expect(page).toContain('class="hfl-refresh-button"')
    expect(page).toContain('class="hfl-list-footer__pagination"')
  })

  it('requires one selected tag to rename or delete from More Actions', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    expect(page).toContain('type="selection"')
    expect(page).toContain('selectedTags.value.length === 1 && !loading.value && !saving.value')
    expect(page).toContain('canManageSelected && openTagDialog(selectedTags[0])')
    expect(page).toContain('canManageSelected && remove(selectedTags[0])')
    expect(page).toContain('class="hfl-list-footer__selected"')
    expect(page).toContain('watch(page, clearSelection)')
    expect(page).toContain('  clearSelection()\n  try {')
  })

  it('uses one dialog for adding and editing name, description, color and preview', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    expect(page).toContain('dialogOpen.value = true')
    expect(page).toContain("t(editingTag ? 'protection.tags.editTitle' : 'protection.tags.createTitle')")
    expect(page).toContain('form.value = tag')
    expect(page).toContain('v-model="form.name"')
    expect(page).toContain('v-model="form.description"')
    expect(page).toContain('v-model="form.color"')
    expect(page).toContain('v-for="color in SOURCE_TAG_COLORS"')
    expect(page).toContain('<SourceTagBadge :tag="preview" :interactive="false" />')
    expect(page).toContain('await updateSourceTag(editingTag.value.id, payload)')
    expect(page).toContain('await createSourceTag(payload)')
    expect(page).not.toContain('ElMessageBox.prompt(')
  })

  it('shows tag name errors inline on blur and blocks invalid submits', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    expect(page).toContain("sourceTagNameIssue(form.value.name, rows.value, editingTag.value?.id)")
    expect(page).toContain(':error="nameError"')
    expect(page).toContain('@blur="validateNameOnBlur"')
    expect(page).toContain('if (saving.value || !validateForm()) return')
    expect(page).toContain('name: form.value.name,')
  })

  it('ellipsizes long preview names without altering the saved tag name', () => {
    const page = readFileSync(resolve(process.cwd(), 'src/pages/protection/SourceTags.vue'), 'utf8')
    const badge = readFileSync(resolve(process.cwd(), 'src/components/SourceTagBadge.vue'), 'utf8')
    const styles = readFileSync(resolve(process.cwd(), 'src/styles/source-tag-ui.css'), 'utf8')
    expect(page).toContain('class="source-tag-preview"')
    expect(page).toContain('width: 100%;')
    expect(badge).toContain(':title="props.showNameTitle ? tag.name : undefined"')
    expect(badge).toContain('showIcon?: boolean')
    expect(badge).toContain('v-if="props.showIcon"')
    expect(badge).toContain('<span class="source-tag-badge__label">{{ tag.name }}</span>')
    expect(styles).toContain('.source-tag-badge__label')
    expect(styles).toContain('text-overflow: ellipsis')
    expect(styles.split('.source-tag-badge__label {')[1]?.split('}')[0]).toContain('overflow: hidden')
  })

  it('shows a single selection border on pointer click and a keyboard-only focus ring', () => {
    const styles = readFileSync(resolve(process.cwd(), 'src/styles/source-tag-ui.css'), 'utf8')
    expect(styles).toContain(".source-tag-swatch[data-selected='true']")
    expect(styles).toContain('.source-tag-swatch:has(input:focus-visible)')
    expect(styles).not.toContain('.source-tag-swatch:focus-within')
  })
})

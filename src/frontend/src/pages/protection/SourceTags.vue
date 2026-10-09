<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown, Pencil, Plus, RefreshCw, Trash2 } from 'lucide-vue-next'
import type { ElTable } from 'element-plus'
import ModulePage from '../../components/ModulePage.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import HflPagination from '../../components/HflPagination.vue'
import SourceTagBadge from '../../components/SourceTagBadge.vue'
import DangerConfirmDialog from '../../components/DangerConfirmDialog.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useResponsiveDrawerWidth } from '../../composables/useResponsiveDrawerWidth'
import { apiErrorMessage } from '../../lib/api'
import { formatAppDateTime } from '../../lib/dateTime'
import { notifyError, notifySuccess } from '../../lib/notify'
import { createSourceTag, deleteSourceTag, listSourceTags, listSourcesForTag, updateSourceTag, type SourceTag, type SourceTagInput, type TaggedSource } from '../../lib/sourceApi'
import { SOURCE_TAG_COLORS } from '../../lib/sourceTagColor'
import { sourceTagNameIssue } from '../../lib/sourceTagName'

const { t } = useI18n()
const menus = useProtectionSideNav()
const { drawerSize } = useResponsiveDrawerWidth(3)
const rows = ref<SourceTag[]>([])
const loading = ref(false)
const saving = ref(false)
const error = ref(false)
const page = ref(1)
const pageSize = ref(30)
const tableRef = ref<InstanceType<typeof ElTable> | null>(null)
const selectedTags = ref<SourceTag[]>([])
const moreActionsOpen = ref(false)
const canManageSelected = computed(() => selectedTags.value.length === 1 && !loading.value && !saving.value)
const visibleRows = computed(() => rows.value.slice((page.value - 1) * pageSize.value, page.value * pageSize.value))
const dialogOpen = ref(false)
const editingTag = ref<SourceTag | null>(null)
const form = ref<SourceTagInput>({ name: '', description: '', color: 'neutral' })
const nameError = ref('')
const descriptionError = ref('')
const preview = computed(() => ({
  name: form.value.name.trim() || t('protection.tags.previewPlaceholder'),
  description: form.value.description.trim(),
  color: form.value.color,
}))
const usageTag = ref<SourceTag | null>(null)
const usageOpen = ref(false)
const usageRows = ref<TaggedSource[]>([])
const usageCount = ref(0)
const usagePage = ref(1)
const usagePageSize = ref(30)
const usageSearch = ref('')
const appliedUsageSearch = ref('')
const usageLoading = ref(false)
const usageError = ref(false)
let usageRequest = 0
let usageController: AbortController | null = null

async function loadUsage() {
  if (!usageOpen.value || !usageTag.value) return
  const request = ++usageRequest
  usageController?.abort()
  const controller = new AbortController()
  usageController = controller
  usageLoading.value = true
  usageError.value = false
  try {
    const result = await listSourcesForTag(usageTag.value.id, {
      page: usagePage.value,
      page_size: usagePageSize.value,
      search: appliedUsageSearch.value || undefined,
    }, { signal: controller.signal })
    if (request !== usageRequest || !usageOpen.value) return
    usageRows.value = result.results
    usageCount.value = result.count
  } catch (e) {
    if (request !== usageRequest || controller.signal.aborted) return
    usageError.value = true
    usageRows.value = []
    notifyError(apiErrorMessage(e, t('errors.pageLoad.loadFailed.title')))
  } finally {
    if (request === usageRequest) usageLoading.value = false
  }
}

function openUsage(tag: SourceTag) {
  usageTag.value = tag
  usageSearch.value = ''
  appliedUsageSearch.value = ''
  usagePage.value = 1
  usageOpen.value = true
  void loadUsage()
}

function applyUsageSearch() {
  appliedUsageSearch.value = usageSearch.value.trim()
  const wasFirstPage = usagePage.value === 1
  usagePage.value = 1
  if (wasFirstPage) void loadUsage()
}

watch(usagePage, () => {
  if (usageOpen.value) void loadUsage()
})
watch(usagePageSize, () => {
  const wasFirstPage = usagePage.value === 1
  usagePage.value = 1
  if (usageOpen.value && wasFirstPage) void loadUsage()
})
watch(usageOpen, (open) => {
  if (!open) {
    usageRequest += 1
    usageController?.abort()
    usageLoading.value = false
  }
})
onUnmounted(() => usageController?.abort())

function clearSelection() {
  selectedTags.value = []
  tableRef.value?.clearSelection()
}

watch(page, clearSelection)
watch(pageSize, () => {
  page.value = 1
  clearSelection()
})

async function load() {
  loading.value = true
  error.value = false
  clearSelection()
  try {
    rows.value = await listSourceTags()
    page.value = Math.min(page.value, Math.max(1, Math.ceil(rows.value.length / pageSize.value)))
  } catch (e) {
    error.value = true
    notifyError(apiErrorMessage(e, t('errors.pageLoad.loadFailed.title')))
  } finally {
    loading.value = false
  }
}

function openTagDialog(tag?: SourceTag) {
  if (saving.value || loading.value) return
  editingTag.value = tag || null
  form.value = tag
    ? { name: tag.name, description: tag.description, color: tag.color }
    : { name: '', description: '', color: 'neutral' }
  nameError.value = ''
  descriptionError.value = ''
  dialogOpen.value = true
}

function validateName(): void {
  const issue = sourceTagNameIssue(form.value.name, rows.value, editingTag.value?.id)
  nameError.value = issue ? t(`protection.tags.nameIssues.${issue}`) : ''
}

function validateNameOnBlur(): void {
  validateName()
}

function validateForm(): boolean {
  const description = form.value.description.trim()
  validateName()
  descriptionError.value = description.length > 255 ? t('protection.tags.descriptionError') : ''
  return !nameError.value && !descriptionError.value
}

async function saveTag() {
  if (saving.value || !validateForm()) return
  const payload: SourceTagInput = {
    name: form.value.name,
    description: form.value.description.trim(),
    color: form.value.color,
  }
  saving.value = true
  try {
    if (editingTag.value) await updateSourceTag(editingTag.value.id, payload)
    else await createSourceTag(payload)
    dialogOpen.value = false
    notifySuccess(t('protection.tags.saved'))
    await load()
  } catch (e) {
    const fields = (e && typeof e === 'object' && 'fields' in e)
      ? (e as { fields?: Record<string, string[]> }).fields
      : undefined
    if (fields?.name?.length) nameError.value = t('protection.tags.nameIssues.invalid')
    else notifyError(apiErrorMessage(e, t('errors.pageLoad.loadFailed.title')))
  } finally {
    saving.value = false
  }
}

const deleteConfirmOpen = ref(false)
const deleteTarget = ref<SourceTag | null>(null)

async function remove(tag: SourceTag, confirmed = false) {
  if (saving.value) return
  if (!confirmed) {
    deleteTarget.value = tag
    deleteConfirmOpen.value = true
    return
  }
  saving.value = true
  try {
    await deleteSourceTag(tag.id)
    deleteConfirmOpen.value = false
    notifySuccess(t('protection.tags.saved'))
    await load()
  } catch (e) {
    notifyError(apiErrorMessage(e, t('errors.pageLoad.loadFailed.title')))
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <ModulePage
    :menus="menus"
    :title="t('protection.tags.title')"
    body-fill
  >
    <div class="hfl-list-shell hfl-list-shell--fill">
      <HflTablePanel fill>
        <template #toolbar>
          <ElButton
            type="primary"
            :disabled="loading || saving"
            @click="openTagDialog()"
          >
            <Plus :size="16" />
            {{ t('protection.tags.create') }}
          </ElButton>
          <ElDropdown
            trigger="click"
            popper-class="hfl-actions-dropdown"
            @visible-change="moreActionsOpen = $event"
          >
            <ElButton :disabled="loading || saving">
              {{ t('protection.tags.moreActions') }}
              <ChevronDown
                :size="16"
                class="hfl-list-more__chev"
                :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
              />
            </ElButton>
            <template #dropdown>
              <ElDropdownMenu>
                <ElDropdownItem
                  :disabled="!canManageSelected"
                  @click="canManageSelected && openTagDialog(selectedTags[0])"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Pencil :size="14" class="shrink-0" />
                    <span>{{ t('protection.tags.edit') }}</span>
                  </span>
                </ElDropdownItem>
                <ElDropdownItem
                  divided
                  class="el-dropdown-menu__item--danger"
                  :disabled="!canManageSelected"
                  @click="canManageSelected && remove(selectedTags[0])"
                >
                  <span class="el-dropdown-menu__item-content">
                    <Trash2 :size="14" class="shrink-0" />
                    <span>{{ t('protection.tags.delete') }}</span>
                  </span>
                </ElDropdownItem>
              </ElDropdownMenu>
            </template>
          </ElDropdown>
        </template>
        <template #toolbar-utility>
          <ElButton
            class="hfl-refresh-button"
            :title="t('common.refresh')"
            :aria-label="t('common.refresh')"
            :disabled="loading || saving"
            @click="load"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': loading }"
            />
          </ElButton>
        </template>
        <template #table="{ tableMaxHeight }">
          <el-table
            ref="tableRef"
            v-loading="loading"
            :data="visibleRows"
            :max-height="tableMaxHeight"
            class="hfl-list-table"
            stripe
            row-key="id"
            @selection-change="selectedTags = $event"
          >
            <el-table-column
              type="selection"
              width="35"
              fixed="left"
            />
            <el-table-column
              :label="t('protection.tags.name')"
              min-width="200"
            >
              <template #default="{ row }">
                <SourceTagBadge :tag="row" :interactive="false" :show-name-title="false" :show-icon="false" />
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.tags.description')"
              min-width="240"
            >
              <template #default="{ row }">
                {{ row.description || '—' }}
              </template>
            </el-table-column>
            <el-table-column
              prop="source_count"
              :label="t('protection.tags.count')"
              min-width="130"
            >
              <template #default="{ row }">
                <ElButton
                  v-if="row.source_count > 0"
                  link
                  :aria-label="t('protection.tags.viewSources', { n: row.source_count })"
                  @click.stop="openUsage(row)"
                >
                  {{ row.source_count }}
                </ElButton>
                <span v-else>0</span>
              </template>
            </el-table-column>
            <el-table-column
              :label="t('protection.tags.createdAt')"
              min-width="180"
            >
              <template #default="{ row }">
                {{ formatAppDateTime(row.created_at) }}
              </template>
            </el-table-column>
            <template #empty>
              <el-empty :description="error ? t('errors.pageLoad.loadFailed.title') : t('protection.tags.empty')">
                <ElButton
                  v-if="error"
                  @click="load"
                >
                  {{ t('common.retry') }}
                </ElButton>
              </el-empty>
            </template>
          </el-table>
        </template>
        <template #footer>
          <span
            v-if="selectedTags.length > 0"
            class="hfl-list-footer__selected"
          >
            {{ t('protection.tags.selectedCount', { n: selectedTags.length }) }}
          </span>
          <HflPagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            class="hfl-list-footer__pagination"
            :total="rows.length"
          />
        </template>
      </HflTablePanel>
    </div>
    <ElDrawer
      v-model="usageOpen"
      class="hfl-detail-drawer"
      direction="rtl"
      :size="drawerSize"
      destroy-on-close
    >
      <template #header>
        <div class="source-tag-usage-title">
          <span>{{ t('protection.tags.usageTitle', { name: usageTag?.name || '' }) }}</span>
          <SourceTagBadge v-if="usageTag" :tag="usageTag" :interactive="false" />
        </div>
      </template>
      <ElInput
        v-model="usageSearch"
        clearable
        :placeholder="t('protection.tags.searchSources')"
        @keyup.enter="applyUsageSearch"
        @clear="applyUsageSearch"
      >
        <template #append>
          <ElButton @click="applyUsageSearch">{{ t('common.search') }}</ElButton>
        </template>
      </ElInput>
      <el-table v-loading="usageLoading" :data="usageRows" stripe>
        <el-table-column prop="name" :label="t('protection.sourceResources.colName')" min-width="175" />
        <el-table-column :label="t('protection.tags.sourceType')" min-width="105">
          <template #default="{ row }">
            {{ t(row.type === 'host' ? 'protection.side.sourceHosts' : 'protection.side.sourceNas') }}
          </template>
        </el-table-column>
        <el-table-column :label="t('protection.sourceResources.colConnectivity')" min-width="105">
          <template #default="{ row }">
            <el-tag :type="row.availability === 'online' ? 'success' : 'info'" size="small">
              {{ t(row.availability === 'online' ? 'protection.backupsPage.step3StatusOnline' : 'protection.backupsPage.step3StatusOffline') }}
            </el-tag>
          </template>
        </el-table-column>
        <template #empty>
          <el-empty :description="t(usageError ? 'errors.pageLoad.loadFailed.title' : 'protection.tags.noSources')">
            <ElButton v-if="usageError" @click="loadUsage">{{ t('common.retry') }}</ElButton>
          </el-empty>
        </template>
      </el-table>
      <div class="hfl-list-footer">
        <HflPagination
          v-model:current-page="usagePage"
          v-model:page-size="usagePageSize"
          class="hfl-list-footer__pagination"
          :total="usageCount"
        />
      </div>
    </ElDrawer>
    <DangerConfirmDialog
      v-model="deleteConfirmOpen"
      :title="t('protection.tags.delete')"
      :message="t('protection.tags.confirmDelete', { name: deleteTarget?.name || '', count: deleteTarget?.source_count || 0 })"
      :cancel-text="t('common.cancel')"
      :confirm-text="t('protection.tags.delete')"
      :loading="saving"
      level="medium"
      @confirm="deleteTarget && remove(deleteTarget, true)"
      @cancel="deleteConfirmOpen = false"
    />
    <el-dialog
      v-model="dialogOpen"
      :title="t(editingTag ? 'protection.tags.editTitle' : 'protection.tags.createTitle')"
      width="560px"
      destroy-on-close
      :close-on-click-modal="false"
      :close-on-press-escape="!saving"
      :show-close="!saving"
    >
      <el-form label-position="top" @submit.prevent="saveTag">
        <el-form-item :label="t('protection.tags.name')" required :error="nameError">
          <el-input
            v-model="form.name"
            maxlength="64"
            show-word-limit
            :placeholder="t('protection.tags.namePlaceholder')"
            @input="nameError = ''"
            @blur="validateNameOnBlur"
          />
          <span class="source-tag-form-hint">{{ t('protection.tags.nameHint') }}</span>
        </el-form-item>
        <el-form-item :label="t('protection.tags.description')" :error="descriptionError">
          <el-input
            v-model="form.description"
            type="textarea"
            :rows="3"
            maxlength="255"
            show-word-limit
            :placeholder="t('protection.tags.descriptionPlaceholder')"
            @input="descriptionError = ''"
          />
        </el-form-item>
        <el-form-item :label="t('protection.tags.color')">
          <div class="source-tag-color-picker" role="radiogroup" :aria-label="t('protection.tags.color')">
            <label
              v-for="color in SOURCE_TAG_COLORS"
              :key="color"
              class="source-tag-swatch"
              :data-color="color"
              :data-selected="form.color === color"
              :title="t(`protection.tags.colorNames.${color}`)"
            >
              <input
                v-model="form.color"
                type="radio"
                class="sr-only"
                name="source-tag-color"
                :value="color"
                :aria-label="t(`protection.tags.colorNames.${color}`)"
              >
              <span class="source-tag-swatch__dot" aria-hidden="true" />
            </label>
          </div>
        </el-form-item>
        <el-form-item :label="t('protection.tags.preview')">
          <div class="source-tag-preview">
            <SourceTagBadge :tag="preview" :interactive="false" />
          </div>
        </el-form-item>
      </el-form>
      <template #footer>
        <ElButton :disabled="saving" @click="dialogOpen = false">
          {{ t('common.cancel') }}
        </ElButton>
        <ElButton type="primary" :loading="saving" @click="saveTag">
          {{ t('common.confirm') }}
        </ElButton>
      </template>
    </el-dialog>
  </ModulePage>
</template>

<style scoped>
.source-tag-color-picker {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.source-tag-preview {
  width: 100%;
  min-width: 0;
}

.source-tag-form-hint {
  color: var(--el-text-color-secondary);
  font-size: 12px;
}

.source-tag-usage-title {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px;
}
</style>

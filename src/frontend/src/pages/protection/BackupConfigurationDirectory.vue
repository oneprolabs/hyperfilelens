<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { RefreshCw, Search } from 'lucide-vue-next'
import ModulePage from '../../components/ModulePage.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import HflPagination from '../../components/HflPagination.vue'
import { useProtectionSideNav } from '../../composables/useProtectionSideNav'
import { useTenantPermissions } from '../../composables/useTenantPermissions'
import { currentUser, getEffectiveOrgKey } from '../../composables/useAuth'
import { usePageRequestScope } from '../../composables/usePageRequestScope'
import { useProtectionLoadState } from '../../composables/useProtectionLoadState'
import { useListSearch } from '../../composables/useListSearch'
import { listBackupConfigs, type BackupConfig } from '../../lib/protectionBackupConfigApi'
import { backupConfigurationDirectoryPath } from '../../lib/protectionAccess'
import { toApiError } from '../../lib/errors/normalizer'
import { formatAppDateTime } from '../../lib/dateTime'

const { t } = useI18n()
const router = useRouter()
const menus = useProtectionSideNav()
const { has } = useTenantPermissions()
const allowed = computed(() => has('protection.backup_configurations.view'))
const requests = usePageRequestScope()
const { error, denied, fail } = useProtectionLoadState()
const rows = ref<BackupConfig[]>([])
const count = ref(0)
const page = ref(1)
const pageSize = ref(30)
const loading = ref(false)
const search = ref('')
const { appliedSearch, runSearchNow, clearSearch, resetSearch } = useListSearch(search, () => {
  page.value = 1
})

const normalized = (values: string[] | undefined) => [...new Set(values || [])].sort()
const authorizationKey = computed(() => {
  const user = currentUser.value
  const profile = user?.access_profile
  return JSON.stringify([
    user?.id, user?.is_staff, user?.requires_password_change,
    profile?.org_key, profile?.role,
    normalized(profile?.permissions), normalized(profile?.group_permissions),
  ])
})

function contextKey() {
  // Storage can change before the verified profile is replaced by the guard.
  return JSON.stringify([getEffectiveOrgKey(), authorizationKey.value])
}

async function load() {
  if (allowed.value && denied.value) return
  const signal = requests.nextSignal('configs')
  const context = contextKey()
  const params = {
    page: page.value,
    page_size: pageSize.value,
    search: appliedSearch.value.trim() || undefined,
    ordering: '-created_at',
  }
  const isCurrent = () => requests.isCurrentSignal('configs', signal)
    && context === contextKey() && allowed.value
    && params.page === page.value && params.page_size === pageSize.value
    && params.search === (appliedSearch.value.trim() || undefined)
  if (!allowed.value) {
    rows.value = []
    count.value = 0
    loading.value = false
    requests.releaseSignal('configs', signal)
    return
  }
  loading.value = true
  error.value = null
  try {
    const result = await listBackupConfigs(params, { signal })
    if (!isCurrent()) return
    rows.value = result.results
    count.value = result.count
  } catch (reason) {
    if (!isCurrent()) return
    rows.value = []
    count.value = 0
    if (toApiError(reason).status === 404 && params.page > 1) {
      // Deletions or scope changes can invalidate the last page. Request the
      // first page once; its response remains authoritative for the new total.
      page.value = 1
      return
    }
    fail(reason)
  } finally {
    if (isCurrent()) loading.value = false
    requests.releaseSignal('configs', signal)
  }
}

function refresh() {
  if (!allowed.value) return
  // Only an explicit user action may retry a same-context authorization error.
  error.value = null
  void load()
}

watch([contextKey, allowed, page, pageSize, appliedSearch], (next, previous) => {
  if (next[0] !== previous?.[0] || next[1] !== previous?.[1]) {
    requests.abortScope('configs')
    rows.value = []
    count.value = 0
    error.value = null
    loading.value = false
    const queryChanged = page.value !== 1 || appliedSearch.value !== ''
    page.value = 1
    resetSearch()
    // Let the coalesced watcher load the reset query once, not the old page.
    if (queryChanged) return
  } else if (next[3] !== previous?.[3] && page.value !== 1) {
    page.value = 1
    return
  }
  void load()
}, { immediate: true })
</script>

<template>
  <ModulePage
    :menus="menus"
    :page-title-override="t('settings.rolesPage.resourceLabels.protection_backup_configurations')"
    body-fill
  >
    <div class="hfl-list-shell hfl-list-shell--fill">
      <HflTablePanel fill>
        <template #toolbar-utility>
          <ElInput
            v-model="search"
            :placeholder="t('common.search')"
            :aria-label="t('common.search')"
            clearable
            class="hfl-list-search w-64"
            :disabled="denied || !allowed"
            @keyup.enter="runSearchNow"
            @clear="clearSearch"
          >
            <template #prefix>
              <Search :size="16" />
            </template>
          </ElInput>
          <ElButton
            class="hfl-refresh-button"
            :title="t('common.refresh')"
            :aria-label="t('common.refresh')"
            :disabled="loading || !allowed"
            @click="refresh"
          >
            <RefreshCw
              :size="16"
              :class="{ 'is-spinning': loading }"
            />
          </ElButton>
        </template>
        <template #table="{ tableMaxHeight }">
          <ElTable
            v-table-overflow-title
            v-loading="loading"
            :data="rows"
            :max-height="tableMaxHeight"
            class="hfl-list-table"
            row-key="id"
            stripe
          >
            <ElTableColumn
              :label="t('settings.resourcesPage.name')"
              min-width="240"
              class-name="hfl-table-name-col"
            >
              <template #default="{ row }">
                <button
                  type="button"
                  class="hfl-table-name-link hfl-table-name-link--full"
                  @click="router.push({
                    path: `/protection/backups/${row.id}`,
                    query: { returnTo: backupConfigurationDirectoryPath },
                  })"
                >
                  {{ row.name }}
                </button>
              </template>
            </ElTableColumn>
            <ElTableColumn
              prop="directory_count"
              :label="t('protection.backupsPage.summaryDirCount')"
              min-width="120"
            />
            <ElTableColumn
              :label="t('protection.backupDetail.colCreated')"
              min-width="180"
            >
              <template #default="{ row }">
                {{ formatAppDateTime(row.created_at) }}
              </template>
            </ElTableColumn>
            <template
              v-if="error || !allowed"
              #empty
            >
              <div role="alert">
                <p>{{ denied || !allowed ? t('errors.codes.authForbidden') : t('protection.backupsPage.backupConfigLoadFailed') }}</p>
                <ElButton
                  v-if="allowed && !denied"
                  link
                  type="primary"
                  @click="load"
                >
                  {{ t('common.retry') }}
                </ElButton>
              </div>
            </template>
          </ElTable>
        </template>
        <template #footer>
          <HflPagination
            v-model:current-page="page"
            v-model:page-size="pageSize"
            :total="count"
            :disabled="loading || !!error || !allowed"
          />
        </template>
      </HflTablePanel>
    </div>
  </ModulePage>
</template>

<script setup lang="ts">
import { onMounted, ref, computed, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api } from '../../lib/api'
import { getEffectiveOrgKey } from '../../composables/useAuth'
import { formatLocalDateTime } from '../../lib/dateTime'
import { asList } from '../../lib/parse'
import ModulePage from '../../components/ModulePage.vue'
import HflTablePanel from '../../components/HflTablePanel.vue'
import HflTypeLabel from '../../components/HflTypeLabel.vue'
import { useNodeSideNav } from '../../composables/useNodeSideNav'
import { booleanStatusTag } from '../../lib/statusTag'

const { t } = useI18n()
const nodeMenus = useNodeSideNav()
/** Commercial plugin frontend present → show org roles; community hides them. */
const showMemberRoles = __HFL_EXTENSIONS_FRONTEND__

type MemberRole = 'owner' | 'admin' | 'operator' | 'auditor' | ''

interface Member {
  id: number
  user: number
  user_email?: string
  user_username?: string
  role: MemberRole
  is_active: boolean
  organization: number
  organization_name?: string
  created_at?: string
}

interface Org {
  id: number
  key: string
  name: string
}

const rows = ref<Member[]>([])
const primaryOrgName = ref('—')

const busy = ref(false)
const currentPage = ref(1)
const pageSize = ref(30)

const paginatedRows = computed(() => {
  const start = (currentPage.value - 1) * pageSize.value
  return rows.value.slice(start, start + pageSize.value)
})

const totalCount = computed(() => rows.value.length)

function formatCreatedAt(iso?: string): string {
  return formatLocalDateTime(iso, '—')
}

function memberDisplayName(member: Member): string {
  return member.user_email || member.user_username || '—'
}

function roleLabel(role: MemberRole): string {
  if (!role) return '—'
  const key = `settings.member.role${role.charAt(0).toUpperCase()}${role.slice(1)}` as
    | 'settings.member.roleOwner'
    | 'settings.member.roleAdmin'
    | 'settings.member.roleOperator'
    | 'settings.member.roleAuditor'
  return t(key)
}

function resolvePrimaryOrg(orgList: Org[]): Org | undefined {
  const orgKey = getEffectiveOrgKey()
  if (orgKey) {
    return orgList.find(o => o.key === orgKey) ?? orgList[0]
  }
  return orgList[0]
}

function membersForPrimaryOrg(members: Member[], orgList: Org[]): Member[] {
  const primaryOrg = resolvePrimaryOrg(orgList)
  if (!primaryOrg) return members
  return members.filter(m => m.organization === primaryOrg.id)
}

async function load() {
  busy.value = true
  try {
    const [membersData, orgsData] = await Promise.all([
      api<unknown>('/api/v1/iam/memberships/'),
      api<unknown>('/api/v1/iam/orgs/'),
    ])
    const orgList = asList<Org>(orgsData)
    const memberList = asList<Member>(membersData)
    primaryOrgName.value = resolvePrimaryOrg(orgList)?.name ?? '—'
    rows.value = membersForPrimaryOrg(memberList, orgList)
  } catch {
    rows.value = []
    primaryOrgName.value = '—'
  } finally {
    busy.value = false
  }
}

onMounted(() => {
  load()
})

watch(pageSize, () => {
  currentPage.value = 1
})
</script>

<template>
  <ModulePage
    :menus="nodeMenus"
    body-fill
  >
    <HflTablePanel fill>
      <template #table="{ tableMaxHeight }">
        <el-table
          v-table-column-resize="'settings.members'"
          v-loading="busy"
          :data="paginatedRows"
          stripe
          row-key="id"
          class="hfl-list-table"
          :max-height="tableMaxHeight"
        >
          <el-table-column
            :label="t('settings.member.colName')"
            min-width="180"
          >
            <template #default="{ row }">
              {{ memberDisplayName(row) }}
            </template>
          </el-table-column>
          <el-table-column
            v-if="showMemberRoles"
            :label="t('settings.member.colRole')"
            width="120"
          >
            <template #default="{ row }">
              <HflTypeLabel :label="roleLabel(row.role)" />
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.member.colOrg')"
            min-width="140"
          >
            <template #default="{ row }">
              {{ row.organization_name || primaryOrgName }}
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.member.colStatus')"
            width="100"
          >
            <template #default="{ row }">
              <el-tag
                v-bind="booleanStatusTag(row.is_active)"
                size="small"
              >
                {{ row.is_active ? t('settings.member.statusActive') : t('settings.member.statusInactive') }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column
            :label="t('settings.member.colCreated')"
            width="170"
          >
            <template #default="{ row }">
              <span
                class="hfl-table-cell-time"
                :class="{ 'hfl-empty-mark': !row.created_at }"
              >{{ formatCreatedAt(row.created_at) }}</span>
            </template>
          </el-table-column>
          <template #empty>
            <el-empty :description="t('settings.member.empty')" />
          </template>
        </el-table>
      </template>

      <template #footer>
        <HflPagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          class="hfl-list-footer__pagination"
          layout="total, sizes, prev, pager, next"
          :total="totalCount"
          :page-sizes="[20, 30, 50, 100]"
        />
      </template>
    </HflTablePanel>
  </ModulePage>
</template>

<style scoped>
:deep(.el-table .el-table__row) {
  cursor: default;
}
</style>

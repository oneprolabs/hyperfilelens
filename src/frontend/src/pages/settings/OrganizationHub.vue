<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { api, isAbortError } from '../../lib/api'
import { currentUser, getEffectiveOrgKey } from '../../composables/useAuth'
import { useTenantPermissions } from '../../composables/useTenantPermissions'
import { formatLocalDateTime } from '../../lib/dateTime'
import { asList } from '../../lib/parse'
import ModulePage from '../../components/ModulePage.vue'
import { useNodeSideNav } from '../../composables/useNodeSideNav'
import { Building2 } from 'lucide-vue-next'

const { t } = useI18n()
const nodeMenus = useNodeSideNav()
const { has } = useTenantPermissions()
const canViewOrganization = computed(() => has('configuration.organizations.view', true))

interface Org {
  id: number
  key: string
  name: string
  created_at?: string
  owner_email?: string
  member_count?: number
}

const organization = ref<Org | null>(null)
const busy = ref(false)
const failed = ref(false)
const forbidden = ref(false)
const accessDenied = computed(() => !canViewOrganization.value || forbidden.value)
let controller: AbortController | null = null

function formatCreatedAt(iso?: string): string {
  return formatLocalDateTime(iso, '—')
}

async function load() {
  controller?.abort()
  organization.value = null
  failed.value = false
  forbidden.value = false
  busy.value = false
  const orgKey = getEffectiveOrgKey()
  if (!canViewOrganization.value || !orgKey) return
  controller = new AbortController()
  const signal = controller.signal
  const isCurrent = () => !signal.aborted
    && canViewOrganization.value && orgKey === getEffectiveOrgKey()
  busy.value = true
  try {
    const orgsData = await api<unknown>('/api/v1/iam/orgs/', { signal })
    if (!isCurrent()) return
    const list = asList<Org>(orgsData)
    organization.value = list.find(o => o.key === orgKey) ?? null
  } catch (error) {
    if (!isCurrent() || isAbortError(error)) return
    forbidden.value = (error as { status?: number } | null)?.status === 403
    failed.value = !forbidden.value
  } finally {
    if (!signal.aborted) busy.value = false
  }
}

// Clear old data before rendering a scope change. Use the default pre-render
// flush so setUser can update both the profile and stored org key first.
watch([
  canViewOrganization,
  () => currentUser.value?.access_profile?.org_key,
  () => getEffectiveOrgKey(),
], () => { void load() }, { immediate: true })
onUnmounted(() => controller?.abort())
</script>

<template>
  <ModulePage
    :menus="nodeMenus"
  >
    <section
      v-loading="busy"
      class="organization-overview"
    >
      <el-card
        v-if="organization"
        class="organization-overview__card"
        shadow="never"
      >
        <div class="organization-overview__header">
          <span
            class="organization-overview__icon"
            aria-hidden="true"
          >
            <Building2 :size="20" />
          </span>
          <h2 class="organization-overview__name">
            {{ organization.name }}
          </h2>
        </div>
        <dl class="organization-overview__details">
          <div class="organization-overview__detail">
            <dt>
              {{ t('settings.org.colKey') }}
            </dt>
            <dd
              :title="t('settings.org.keyHint')"
              tabindex="0"
            >
              {{ organization.key }}
            </dd>
          </div>
          <div class="organization-overview__detail">
            <dt>
              {{ t('settings.org.colMembers') }}
            </dt>
            <dd>
              {{ organization.member_count ?? '—' }}
            </dd>
          </div>
          <div class="organization-overview__detail">
            <dt>
              {{ t('settings.org.colOwner') }}
            </dt>
            <dd
              :class="{ 'hfl-empty-mark': !organization.owner_email }"
            >
              {{ organization.owner_email || '—' }}
            </dd>
          </div>
          <div class="organization-overview__detail">
            <dt>
              {{ t('settings.org.colCreated') }}
            </dt>
            <dd
              :class="{ 'hfl-empty-mark': !organization.created_at }"
            >
              {{ formatCreatedAt(organization.created_at) }}
            </dd>
          </div>
        </dl>
      </el-card>
      <el-empty
        v-else
        :description="t(accessDenied ? 'settings.org.accessDenied' : failed ? 'settings.org.loadFailed' : 'settings.org.empty')"
      >
        <el-button
          v-if="!accessDenied"
          :disabled="busy"
          @click="load"
        >
          {{ t(failed ? 'common.retry' : 'common.refresh') }}
        </el-button>
      </el-empty>
    </section>
  </ModulePage>
</template>

<style scoped>
.organization-overview {
  padding: 20px 16px 32px;
}

.organization-overview__card {
  width: min(100%, 760px);
  margin: 0;
}

.organization-overview__header {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding-bottom: 1rem;
  border-bottom: 1px solid #f1f3f5;
}

.organization-overview__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 2.5rem;
  height: 2.5rem;
  flex: 0 0 2.5rem;
  border-radius: 0.625rem;
  color: var(--dashboard-primary, #5b4bdb);
  background: color-mix(in srgb, var(--dashboard-primary, #5b4bdb) 10%, transparent);
}

.organization-overview__name {
  margin: 0;
  color: #1d2129;
  font-size: 18px;
  font-weight: 600;
  line-height: 1.35;
}

.organization-overview__details {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 1rem 2rem;
  margin: 0;
  padding-top: 1.25rem;
}

.organization-overview__detail {
  min-width: 0;
}

.organization-overview__detail dt {
  color: #86909c;
  font-size: 12px;
  font-weight: 500;
  line-height: 1.4;
}

.organization-overview__detail dd {
  margin: 0.35rem 0 0;
  overflow-wrap: anywhere;
  color: #1d2129;
  font-size: 13px;
  line-height: 1.5;
}

@media (max-width: 767px) {
  .organization-overview {
    padding: 12px 8px 24px;
  }

  .organization-overview__details {
    grid-template-columns: 1fr;
  }
}
</style>

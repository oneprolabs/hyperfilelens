<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { api, apiErrorMessage } from '../../lib/api'
import { unwrapApiPayload } from '../../lib/parse'
import { getEffectiveOrgKey } from '../../composables/useAuth'

const { t } = useI18n()

type DrFieldType = 'number' | 'boolean'

type DrFieldDef = {
  key: string
  valueType: DrFieldType
  labelKey: string
  hintKey: string
  sourceKey?: string
  defaultValue?: string | number | boolean
  min?: number
  max?: number
}

const DR_FIELDS: DrFieldDef[] = [
  {
    key: 'file_dr.dr_task_concurrency',
    valueType: 'number',
    labelKey: 'settings.global.labels.concurrency',
    hintKey: 'settings.global.hints.concurrency',
    sourceKey: 'settings.systemOrg.valueSource',
    defaultValue: 10,
    min: 1,
    max: 256,
  },
]

type OrgSettingRow = {
  key: string
  value: unknown
  value_source?: 'tenant' | 'global' | 'default'
  has_tenant_override?: boolean
}

type OrgSettingsResponse = {
  org_key: string
  settings: OrgSettingRow[]
}

const busy = ref(false)
const saving = ref(false)
const orgKey = ref('')
const sources = reactive<Record<string, string>>({})
const form = reactive<Record<string, string | number | boolean>>({})

const sourceLabel = computed(() => ({
  tenant: t('settings.systemOrg.sourceTenant'),
  global: t('settings.systemOrg.sourceGlobal'),
  default: t('settings.systemOrg.sourceDefault'),
}))

function defaultFor(f: DrFieldDef): string | number | boolean {
  if (f.defaultValue !== undefined) return f.defaultValue
  if (f.valueType === 'number') return 0
  return false
}

function initForm(settings: OrgSettingRow[]) {
  for (const f of DR_FIELDS) {
    const row = settings.find((s) => s.key === f.key)
    if (row && row.value !== undefined && row.value !== null) {
      if (f.valueType === 'boolean') {
        form[f.key] = Boolean(row.value)
      } else {
        const n = Number(row.value)
        form[f.key] = Number.isFinite(n) ? n : (defaultFor(f) as number)
      }
      sources[f.key] = row.value_source || 'default'
    } else {
      form[f.key] = defaultFor(f)
      sources[f.key] = 'default'
    }
  }
}

async function load() {
  orgKey.value = getEffectiveOrgKey()
  if (!orgKey.value) return
  busy.value = true
  try {
    const raw = await api<unknown>('/api/v1/configuration/org-settings/')
    const data = unwrapApiPayload<OrgSettingsResponse>(raw)
    initForm(data.settings || [])
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('settings.global.errLoad')), grouping: true })
  } finally {
    busy.value = false
  }
}

async function save() {
  if (!orgKey.value) return
  saving.value = true
  try {
    const settings = DR_FIELDS.map((f) => ({
      key: f.key,
      value: form[f.key],
    }))
    const raw = await api<unknown>('/api/v1/configuration/org-settings/', {
      method: 'PATCH',
      body: JSON.stringify({ settings }),
    })
    const data = unwrapApiPayload<OrgSettingsResponse>(raw)
    initForm(data.settings || [])
    ElMessage.success({ message: t('settings.systemOrg.msgSaved'), grouping: true })
  } catch (err) {
    ElMessage.error({ message: apiErrorMessage(err, t('settings.global.errSave')), grouping: true })
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div
    v-loading="busy"
    class="hfl-list-shell global-settings"
  >
    <div class="hfl-list-panel">
      <div class="global-settings__panel">
        <section
          v-for="f in DR_FIELDS"
          :key="f.key"
          class="global-settings__row"
        >
          <div class="global-settings__row-text">
            <div class="global-settings__label">
              {{ t(f.labelKey) }}
            </div>
            <p class="global-settings__desc">
              {{ t(f.hintKey) }}
            </p>
            <p
              v-if="sources[f.key]"
              class="global-settings__source"
            >
              {{ t('settings.systemOrg.effectiveFrom', { source: sourceLabel[sources[f.key] as keyof typeof sourceLabel] || sources[f.key] }) }}
            </p>
          </div>
          <div class="global-settings__row-control">
            <ElInputNumber
              v-if="f.valueType === 'number'"
              v-model="form[f.key]"
              :min="f.min ?? 0"
              :max="f.max ?? 999999"
              :step="1"
              controls-position="right"
              class="global-settings__input-num"
            />
            <ElSwitch
              v-else
              v-model="form[f.key]"
            />
          </div>
        </section>
      </div>

      <div class="global-settings__footer">
        <ElButton
          type="primary"
          :loading="saving"
          @click="save"
        >
          {{ t('common.save') }}
        </ElButton>
      </div>
    </div>
  </div>
</template>

<style scoped>
.global-settings {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
}

.global-settings__panel {
  width: 100%;
  box-sizing: border-box;
  background: var(--color-card-bg);
  border-radius: var(--radius-card);
  box-shadow: 0 1px 2px rgb(15 23 42 / 4%);
}

.global-settings__row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  justify-content: space-between;
  gap: 1rem 1.5rem;
  padding: 1.15rem 1.5rem;
  border-bottom: 1px solid var(--el-border-color-lighter);
}

.global-settings__row:last-of-type {
  border-bottom: none;
}

.global-settings__row-text {
  flex: 1 1 12rem;
  min-width: 0;
}

.global-settings__label {
  font-size: 0.9375rem;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.global-settings__desc {
  margin: 0.35rem 0 0;
  font-size: 0.8125rem;
  line-height: 1.5;
  color: var(--el-text-color-secondary);
}

.global-settings__source {
  margin: 0.35rem 0 0;
  font-size: 0.75rem;
  color: var(--el-text-color-placeholder);
}

.global-settings__row-control {
  flex: 0 0 auto;
}

.global-settings__input-num {
  width: 8.5rem;
}

.global-settings__footer {
  display: flex;
  justify-content: flex-end;
  padding: 1rem 1.5rem 0;
}
</style>

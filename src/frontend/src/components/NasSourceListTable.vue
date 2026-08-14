<script setup lang="ts">
import { nasMountProtocolIcon } from '../lib/resourceIcons'
import type { SourceResource } from '../lib/sourceApi'
import type { ApiNode } from '../types/node'
import HflCapacityCell from './HflCapacityCell.vue'
import HflHelpTip from './HflHelpTip.vue'
import {
  NAS_SOURCE_TABLE_HEADER_STYLE,
  useNasSourceListDisplay,
} from '../composables/useNasSourceListDisplay'
import { toRef } from 'vue'
import { useI18n } from 'vue-i18n'

const props = withDefaults(
  defineProps<{
    rows: SourceResource[]
    proxyNodes: ApiNode[]
    loading?: boolean
    selectable?: boolean
    maxHeight?: string | number
    capacitySyncing?: boolean
    emptyDescription?: string
  }>(),
  {
    loading: false,
    selectable: false,
    maxHeight: undefined,
    capacitySyncing: false,
    emptyDescription: '',
  },
)

const emit = defineEmits<{
  'row-click': [SourceResource]
  'selection-change': [SourceResource[]]
}>()

const { t } = useI18n()
const display = useNasSourceListDisplay(toRef(props, 'proxyNodes'))
</script>

<template>
  <el-table
    v-table-overflow-title
    v-table-header-scroll-sync
    v-loading="loading"
    row-key="id"
    :data="rows"
    stripe
    class="hfl-list-table nas-source-list-table"
    :max-height="maxHeight"
    :header-cell-style="NAS_SOURCE_TABLE_HEADER_STYLE"
    @selection-change="emit('selection-change', $event)"
  >
    <el-table-column
      v-if="selectable"
      type="selection"
      width="35"
      fixed="left"
    />
    <el-table-column
      :label="t('protection.sourceResources.colName')"
      min-width="200"
      :fixed="selectable ? undefined : 'left'"
      class-name="hfl-table-name-col"
    >
      <template #default="{ row }">
        <button
          type="button"
          class="hfl-table-name-link hfl-table-name-link--full"
          @click="emit('row-click', row)"
        >
          {{ row.name }}
        </button>
      </template>
    </el-table-column>
    <el-table-column
      :label="t('protection.sourceResources.colProtocol')"
      width="100"
    >
      <template #default="{ row }">
        <span
          v-if="display.nasProtocolType(row) !== 'nas'"
          :class="display.nasProtocolPillClass(row)"
        >
          <component
            :is="nasMountProtocolIcon(display.nasProtocolType(row))"
            :size="12"
            stroke-width="2.25"
          />
          {{ display.nasProtocolLabel(row) }}
        </span>
        <span
          v-else
          class="hfl-empty-mark"
        >—</span>
      </template>
    </el-table-column>
    <el-table-column
      :label="t('protection.sourceResources.colNasServer')"
      min-width="140"
    >
      <template #default="{ row }">
        {{ display.nasServerAddress(row) }}
      </template>
    </el-table-column>
    <el-table-column
      :label="t('protection.sourceResources.colNasShareExport')"
      min-width="160"
    >
      <template #default="{ row }">
        <div class="table-stack-cell">
          <span class="table-stack-cell__secondary">{{ display.nasPathKindLabel(row) }}</span>
          <span class="table-stack-cell__primary">{{ display.nasShareOrExport(row) }}</span>
        </div>
      </template>
    </el-table-column>
    <el-table-column
      :label="t('protection.sourceResources.colSourceProxy')"
      min-width="150"
    >
      <template #header>
        <span class="hfl-table-header-with-tip">
          <span>{{ t('protection.sourceResources.colSourceProxy') }}</span>
          <HflHelpTip
            :content="t('protection.sourceResources.sourceProxyColumnTip')"
            :aria-label="t('protection.sourceResources.sourceProxyColumnHelp')"
          />
        </span>
      </template>
      <template #default="{ row }">
        <div class="table-stack-cell">
          <span
            class="table-stack-cell__primary"
            :class="{ 'hfl-empty-mark': !display.nasSourceProxyName(row) }"
          >{{ display.nasSourceProxyName(row) || '—' }}</span>
          <span
            v-if="display.nasSourceProxyIp(row)"
            class="table-stack-cell__secondary"
          >
            {{ display.nasSourceProxyIp(row) }}
          </span>
        </div>
      </template>
    </el-table-column>
    <el-table-column
      :label="t('protection.sourceResources.colProxyMountPoint')"
      min-width="220"
    >
      <template #default="{ row }">
        <span class="hfl-table-cell-full hfl-table-no-tooltip">{{ display.nasProxyMountPoint(row) }}</span>
      </template>
    </el-table-column>
    <el-table-column
      :label="t('protection.sourceResources.colCapacity')"
      min-width="200"
    >
      <template #default="{ row }">
        <HflCapacityCell
          v-if="row.total_size"
          :used-bytes="Number(row.used_size || 0)"
          :total-bytes="Number(row.total_size || 0)"
          variant="compact"
          :format-bytes="display.formatBytes"
        />
        <span
          v-else-if="display.sourceNodeOnlineStatus(row) === 'online' && capacitySyncing"
          class="source-capacity-pending"
        >
          {{ t('protection.sourceResources.capacitySyncing') }}
        </span>
        <span
          v-else
          class="hfl-empty-mark"
        >—</span>
      </template>
    </el-table-column>
    <el-table-column
      :label="t('protection.sourceResources.colStatus')"
      width="88"
    >
      <template #default="{ row }">
        <div class="hfl-table-no-tooltip">
          <el-tag
            :type="display.sourceNodeOnlineTagType(row)"
            size="small"
          >
            {{ display.sourceNodeOnlineLabel(row) }}
          </el-tag>
        </div>
      </template>
    </el-table-column>
    <el-table-column
      :label="t('protection.sourceResources.colRegistered')"
      min-width="170"
    >
      <template #default="{ row }">
        <span
          class="hfl-table-cell-time"
          :class="{ 'hfl-empty-mark': !display.sourceRegisteredAt(row) }"
        >{{ display.formatDate(display.sourceRegisteredAt(row)) }}</span>
      </template>
    </el-table-column>
    <template #empty>
      <el-empty
        :description="emptyDescription || t('protection.sourceResources.emptyNasSources')"
        :image-size="80"
      />
    </template>
  </el-table>
</template>

<style scoped>
.hfl-table-header-with-tip {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  min-width: 0;
}
</style>

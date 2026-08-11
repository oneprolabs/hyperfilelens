<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import NodeLifecycleWizard from './NodeLifecycleWizard.vue'
import { getEffectiveOrgKey } from '../composables/useAuth'
import { copyTextToClipboard } from '../lib/clipboard'
import type { EnrollmentOs } from '../lib/nodeApi'
import type { ApiNode, NodeRole } from '../types/node'

const props = defineProps<{
  modelValue: boolean
  node: ApiNode | null
  initialTab?: 'install' | 'upgrade' | 'uninstall' | 'service'
}>()

const emit = defineEmits<{
  'update:modelValue': [boolean]
}>()

const { t } = useI18n()
const orgKey = computed(() => getEffectiveOrgKey())
const os = ref<EnrollmentOs>('linux')
const role = ref<NodeRole>('agent')

watch(
  () => props.node,
  (node) => {
    if (!node) return
    role.value = node.role
    const meta = node.metadata as Record<string, unknown> | undefined
    const platform = String(meta?.platform ?? '').toLowerCase()
    if (platform === 'windows') os.value = 'windows'
    else if (platform === 'darwin' || platform === 'macos') os.value = 'macos'
    else os.value = 'linux'
  },
  { immediate: true },
)

function close() {
  emit('update:modelValue', false)
}

async function copyCommand(text: string) {
  if (!text) return
  try {
    await copyTextToClipboard(text)
    ElMessage.success({ message: t('nodesDeploy.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('nodesDeploy.copyFailed'), grouping: true })
  }
}
</script>

<template>
  <ElDrawer
    :model-value="modelValue"
    :title="t('nodeLifecycle.drawerTitle', { name: node?.name ?? '—' })"
    size="640px"
    destroy-on-close
    @update:model-value="emit('update:modelValue', $event)"
    @close="close"
  >
    <NodeLifecycleWizard
      v-if="node"
      :org-key="orgKey"
      :node-id="node.id"
      :role="role"
      :os="os"
      :initial-tab="initialTab ?? 'upgrade'"
      role-locked
      @update:os="os = $event"
      @copy="copyCommand"
    />
  </ElDrawer>
</template>

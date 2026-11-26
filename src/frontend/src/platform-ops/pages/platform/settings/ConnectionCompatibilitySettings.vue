<script setup lang="ts">
import { onBeforeUnmount, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ElMessage } from 'element-plus'
import { apiErrorMessage } from '../../../../lib/api'
import { patchPlatformConnectionCompatibility } from '../../../lib/platformOpsApi'

const props = defineProps<{ initialValue: boolean }>()
const emit = defineEmits<{ saved: [enabled: boolean] }>()
const { t } = useI18n()
// Address saves can return an older compatibility snapshot. This row owns
// its save state independently and initializes only when it is mounted.
const savedValue = ref(props.initialValue)
const enabled = ref(props.initialValue)
const saving = ref(false)
let active = true
onBeforeUnmount(() => { active = false })

async function save() {
  if (saving.value || enabled.value === savedValue.value) return
  saving.value = true
  try {
    const result = await patchPlatformConnectionCompatibility(enabled.value)
    if (!active) return
    const value = result.compatibility?.aliyun_sts_proxy_compatibility_enabled
    if (typeof value !== 'boolean') throw new Error(t('platformOps.settings.saveFailed'))
    savedValue.value = value
    enabled.value = value
    emit('saved', value)
    ElMessage.success({ message: t('platformOps.settings.saveSuccess'), grouping: true })
  } catch (error) {
    if (active) {
      enabled.value = savedValue.value
      ElMessage.error({
        message: apiErrorMessage(error, t('platformOps.settings.saveFailed')),
        grouping: true,
      })
    }
  } finally {
    if (active) saving.value = false
  }
}
</script>

<template>
  <div
    class="platform-settings__setting-row"
    data-testid="sts-compatibility"
  >
    <div class="platform-settings__setting-label">
      <label for="aliyun-sts-proxy-compatibility">
        {{ t('platformOps.settings.externalAccess.aliyunStsProxyCompatibility') }}
      </label>
    </div>
    <div class="platform-settings__setting-control">
      <el-switch
        id="aliyun-sts-proxy-compatibility"
        v-model="enabled"
        :disabled="saving"
        :loading="saving"
        :aria-label="t('platformOps.settings.externalAccess.aliyunStsProxyCompatibility')"
        @change="save"
      />
      <p class="platform-settings__hint">
        {{ t('platformOps.settings.externalAccess.aliyunStsProxyCompatibilityHint') }}
      </p>
    </div>
  </div>
</template>

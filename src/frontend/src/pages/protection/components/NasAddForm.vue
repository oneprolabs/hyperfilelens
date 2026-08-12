<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Plus, RefreshCw } from 'lucide-vue-next'
import { nasProtocolNfsIcon, nasProtocolSmbIcon } from '../../../lib/resourceIcons'
import { proxyNodeSelectLine } from '../../../lib/nodeInventoryDisplay'
import type { ApiNode } from '../../../types/node'

type NasProtocol = 'smb' | 'nfs'

const props = defineProps<{
  protocol: NasProtocol
  smbServer: string
  smbShare: string
  smbUsername: string
  smbPassword: string
  smbDomain: string
  nfsHost: string
  nfsExport: string
  nfsOptions: string
  bindNodeId: number | undefined
  bindNodeError: string
  name: string
  generatedName: string
  nameConflictMessage: string
  dir: string
  generatedDir: string
  proxyNodes: ApiNode[]
  proxyNodesRefreshing: boolean
  busy: boolean
}>()

const emit = defineEmits<{
  'update:protocol': [NasProtocol]
  'update:smbServer': [string]
  'update:smbShare': [string]
  'update:smbUsername': [string]
  'update:smbPassword': [string]
  'update:smbDomain': [string]
  'update:nfsHost': [string]
  'update:nfsExport': [string]
  'update:nfsOptions': [string]
  'update:bindNodeId': [number | undefined]
  'update:name': [string]
  nameTouched: []
  'update:dir': [string]
  dirTouched: []
  clearBindNodeError: []
  refreshProxyNodes: []
  openProxyDeploy: []
  cancel: []
  submit: []
}>()

const { t } = useI18n()
const bindSectionRef = ref<HTMLElement | null>(null)

const onlineProxyNodes = computed(() => props.proxyNodes.filter((n) => n.availability === 'online'))

function proxyNodeStatusLabel(node: ApiNode) {
  return node.availability === 'online'
    ? t('protection.sourceResources.nodeStatusOnline')
    : t('protection.sourceResources.nodeStatusOffline')
}

function proxyNodeStatusTagType(node: ApiNode): 'success' | 'danger' | 'warning' | 'info' {
  if (node.availability === 'online') return 'success'
  if (node.status === 'offline') return 'danger'
  return 'info'
}

function proxySelectEmptyHint(): string {
  if (props.proxyNodes.length === 0) return t('protection.sourceResources.nasNoProxy')
  if (onlineProxyNodes.value.length === 0) return t('protection.sourceResources.rebindProxyNoOnline')
  return ''
}

function scrollToBindNode() {
  bindSectionRef.value?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
}

defineExpose({
  scrollToBindNode,
})
</script>

<template>
  <div class="nas-add-form">
    <div class="fullscreen-form-step-stack source-deploy-nas-stack">
      <div class="fullscreen-form-card source-deploy-nas-protocol-card">
        <section class="fullscreen-form-section">
          <h3 class="fullscreen-form-section__title">
            <span class="fullscreen-form-section__indicator" />
            {{ t('repositoriesPage.fieldProtocol') }}
          </h3>

          <ElRadioGroup
            :model-value="protocol"
            class="add-nas-protocol-grid"
            @update:model-value="emit('update:protocol', $event as NasProtocol)"
          >
            <ElRadio
              value="smb"
              border
              class="add-nas-protocol-card !mr-0"
            >
              <div class="add-nas-protocol-card__inner">
                <span class="add-nas-protocol-card__icon">
                  <component :is="nasProtocolSmbIcon" :size="20" stroke-width="2" />
                </span>
                <div class="add-nas-protocol-card__text">
                  <div class="add-nas-protocol-card__title">
                    {{ t('repositoriesPage.protocolSmb') }}
                  </div>
                </div>
              </div>
            </ElRadio>
            <ElRadio
              value="nfs"
              border
              class="add-nas-protocol-card !mr-0"
            >
              <div class="add-nas-protocol-card__inner">
                <span class="add-nas-protocol-card__icon">
                  <component :is="nasProtocolNfsIcon" :size="20" stroke-width="2" />
                </span>
                <div class="add-nas-protocol-card__text">
                  <div class="add-nas-protocol-card__title">
                    {{ t('repositoriesPage.protocolNfs') }}
                  </div>
                </div>
              </div>
            </ElRadio>
          </ElRadioGroup>
        </section>
      </div>

      <div class="fullscreen-form-card">
        <section class="fullscreen-form-section">
          <h3 class="fullscreen-form-section__title">
            <span class="fullscreen-form-section__indicator" />
            {{ t('protection.sourceResources.nasStepConnection') }}
          </h3>

          <ElForm
            label-position="top"
            class="add-nas-form fullscreen-form-el-form"
          >
            <div v-if="protocol === 'smb'" class="add-nas-form-row add-nas-form-row--triple">
              <ElFormItem :label="t('addNasRepo.fieldSmbHost')" required class="flex-1">
                <ElInput
                  :model-value="smbServer"
                  :placeholder="t('protection.sourceResources.nasPhSmbHost')"
                  @update:model-value="emit('update:smbServer', $event as string)"
                />
                <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintSmbHost') }}</p>
              </ElFormItem>
              <ElFormItem :label="t('addNasRepo.fieldSmbShare')" required class="flex-1">
                <ElInput
                  :model-value="smbShare"
                  :placeholder="t('protection.sourceResources.nasPhSmbShare')"
                  @update:model-value="emit('update:smbShare', $event as string)"
                />
                <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintSmbShare') }}</p>
              </ElFormItem>
              <ElFormItem :label="t('addNasRepo.fieldMountOptions')" class="flex-1">
                <ElInput
                  :model-value="nfsOptions"
                  :placeholder="t('protection.sourceResources.nasPhMountOptionsSmb')"
                  @update:model-value="emit('update:nfsOptions', $event as string)"
                />
                <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintMountOptionsSmb') }}</p>
              </ElFormItem>
            </div>

            <div v-else class="add-nas-form-row add-nas-form-row--triple">
              <ElFormItem :label="t('addNasRepo.fieldNfsHost')" required class="flex-1">
                <ElInput
                  :model-value="nfsHost"
                  :placeholder="t('protection.sourceResources.nasPhNfsHost')"
                  @update:model-value="emit('update:nfsHost', $event as string)"
                />
                <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintNfsHost') }}</p>
              </ElFormItem>
              <ElFormItem :label="t('addNasRepo.fieldNfsExport')" required class="flex-1">
                <ElInput
                  :model-value="nfsExport"
                  :placeholder="t('protection.sourceResources.nasPhNfsExport')"
                  @update:model-value="emit('update:nfsExport', $event as string)"
                />
                <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintNfsExport') }}</p>
              </ElFormItem>
              <ElFormItem :label="t('addNasRepo.fieldMountOptions')" class="flex-1">
                <ElInput
                  :model-value="nfsOptions"
                  :placeholder="t('protection.sourceResources.nasPhMountOptionsNfs')"
                  @update:model-value="emit('update:nfsOptions', $event as string)"
                />
                <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintMountOptionsNfs') }}</p>
              </ElFormItem>
            </div>
          </ElForm>

          <template v-if="protocol === 'smb'">
            <ElForm
              label-position="top"
              class="add-nas-form add-nas-form--auth fullscreen-form-el-form"
            >
              <div class="add-nas-form-row add-nas-form-row--triple">
                <ElFormItem :label="t('protection.sourceResources.nasFieldSmbUsername')" required class="flex-1">
                  <ElInput
                    :model-value="smbUsername"
                    :placeholder="t('protection.sourceResources.nasPhSmbUsername')"
                    @update:model-value="emit('update:smbUsername', $event as string)"
                  />
                  <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintSmbUsername') }}</p>
                </ElFormItem>
                <ElFormItem :label="t('protection.sourceResources.nasFieldSmbPassword')" required class="flex-1">
                  <ElInput
                    :model-value="smbPassword"
                    type="password"
                    show-password
                    :placeholder="t('protection.sourceResources.nasPhSmbPassword')"
                    @update:model-value="emit('update:smbPassword', $event as string)"
                  />
                  <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintSmbPassword') }}</p>
                </ElFormItem>
                <ElFormItem :label="t('protection.sourceResources.nasFieldSmbDomain')" class="flex-1">
                  <ElInput
                    :model-value="smbDomain"
                    :placeholder="t('protection.sourceResources.nasPhSmbDomain')"
                    @update:model-value="emit('update:smbDomain', $event as string)"
                  />
                  <p class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintSmbDomain') }}</p>
                </ElFormItem>
              </div>
            </ElForm>
          </template>
        </section>
      </div>

      <div ref="bindSectionRef" class="fullscreen-form-card">
        <section class="fullscreen-form-section">
          <h3 class="fullscreen-form-section__title">
            <span class="fullscreen-form-section__indicator" />
            {{ t('protection.sourceResources.nasFieldBindNode') }}
          </h3>

          <ElForm
            label-position="top"
            class="add-nas-form fullscreen-form-el-form"
          >
            <div class="add-nas-form-row add-nas-form-row--pair add-nas-bind-row">
              <ElFormItem
                required
                class="add-nas-bind-form-item flex-1"
              >
                <template #label>
                  {{ t('protection.sourceResources.nasFieldProxyNode') }}
                </template>

                <div class="add-nas-control-field add-nas-select-row">
                  <div class="add-nas-select-row__controls">
                    <ElSelect
                      :model-value="bindNodeId"
                      class="add-nas-select-row__select"
                      :class="{ 'is-error': !!bindNodeError }"
                      :placeholder="t('protection.sourceResources.selectSourceProxy')"
                      :disabled="onlineProxyNodes.length === 0"
                      :loading="proxyNodesRefreshing"
                      filterable
                      fit-input-width
                      popper-class="nas-add-proxy-select-popper"
                      @update:model-value="emit('update:bindNodeId', $event as number)"
                      @change="emit('clearBindNodeError')"
                    >
                      <ElOption
                        v-for="node in onlineProxyNodes"
                        :key="node.id"
                        :value="node.id"
                        :label="proxyNodeSelectLine(node)"
                      >
                        <div class="nas-add-proxy-option">
                          <span class="nas-add-proxy-option__name">{{ proxyNodeSelectLine(node) }}</span>
                          <ElTag
                            size="small"
                            :type="proxyNodeStatusTagType(node)"
                            effect="plain"
                          >
                            {{ proxyNodeStatusLabel(node) }}
                          </ElTag>
                        </div>
                      </ElOption>
                    </ElSelect>
                    <ElButton
                      class="hfl-refresh-button add-nas-select-row__refresh"
                      :title="t('protection.sourceResources.proxyRefresh')"
                      :aria-label="t('protection.sourceResources.proxyRefresh')"
                      :disabled="proxyNodesRefreshing"
                      @click="emit('refreshProxyNodes')"
                    >
                      <RefreshCw :size="16" :class="{ 'is-spinning': proxyNodesRefreshing }" />
                    </ElButton>
                    <ElButton
                      class="fullscreen-form-icon-btn add-nas-select-row__deploy"
                      :title="t('protection.sourceResources.nasDeployProxy')"
                      :aria-label="t('protection.sourceResources.nasDeployProxy')"
                      @click="emit('openProxyDeploy')"
                    >
                      <Plus :size="14" />
                    </ElButton>
                  </div>
                </div>
                <p
                  class="add-nas-field-hint add-nas-bind-row__proxy-hint"
                  :class="{ 'add-nas-bind-row__proxy-hint--warn': !!(bindNodeError || proxySelectEmptyHint()) }"
                >
                  {{ bindNodeError || proxySelectEmptyHint() || t('protection.sourceResources.nasHintBindProxy') }}
                </p>
              </ElFormItem>
              <ElFormItem :label="t('protection.sourceResources.nasFieldDir')" required class="add-nas-dir-form-item flex-1">
                <div class="add-nas-control-field">
                  <ElInput
                    :model-value="dir"
                    :placeholder="generatedDir"
                    @update:model-value="emit('update:dir', $event as string)"
                    @input="emit('dirTouched')"
                  />
                </div>
                <p class="add-nas-field-hint add-nas-bind-row__dir-hint">{{ t('protection.sourceResources.nasFieldDirHint') }}</p>
              </ElFormItem>
            </div>
          </ElForm>
        </section>
      </div>

      <div class="fullscreen-form-card">
        <section class="fullscreen-form-section">
          <h3 class="fullscreen-form-section__title">
            <span class="fullscreen-form-section__indicator" />
            {{ t('protection.sourceResources.nasStepRepo') }}
          </h3>
          <ElForm
            label-position="top"
            class="add-nas-form fullscreen-form-el-form"
          >
            <ElFormItem :label="t('protection.sourceResources.nasFieldName')" required class="flex-1">
              <ElInput
                :model-value="name"
                :placeholder="generatedName"
                @update:model-value="emit('update:name', $event as string)"
                @input="emit('nameTouched')"
              />
              <p v-if="nameConflictMessage" class="add-nas-field-hint add-nas-field-hint--warn">
                {{ nameConflictMessage }}
              </p>
              <p v-else class="add-nas-field-hint">{{ t('protection.sourceResources.nasHintName') }}</p>
            </ElFormItem>
          </ElForm>
        </section>
      </div>
    </div>

    <div class="fullscreen-form-footer fullscreen-form-action-footer">
      <ElButton @click="emit('cancel')">
        {{ t('common.back') }}
      </ElButton>
      <ElButton
        type="primary"
        :loading="busy"
        :disabled="busy || !!nameConflictMessage"
        @click="emit('submit')"
      >
        {{ t('protection.sourceResources.nasBtnSubmit') }}
      </ElButton>
    </div>
  </div>
</template>

<style scoped>
.nas-add-form {
  width: 100%;
  min-width: 0;
}

.source-deploy-nas-stack {
  gap: 16px;
}

.source-deploy-nas-protocol-card .fullscreen-form-section {
  padding-top: 16px;
  padding-bottom: 20px;
}

.source-deploy-nas-protocol-card .fullscreen-form-section__title {
  margin-bottom: 14px;
}

.add-nas-form {
  width: 100%;
  max-width: none;
  margin-top: 4px;
}

.add-nas-protocol-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
  width: 100%;
}

.add-nas-protocol-grid :deep(.el-radio) {
  width: 100%;
  height: auto;
  margin-right: 0 !important;
}

.add-nas-protocol-grid :deep(.add-nas-protocol-card) {
  width: 100%;
  height: auto !important;
  min-height: 58px;
  padding: 10px 14px !important;
  border-radius: 10px !important;
  border-color: var(--color-border, #e9e9ef) !important;
  background: var(--color-card-bg, #fff);
  box-shadow: none;
  transition: border-color 0.15s ease, background 0.15s ease, box-shadow 0.15s ease, transform 0.15s ease;
}

.add-nas-protocol-grid :deep(.add-nas-protocol-card:hover) {
  border-color: color-mix(in srgb, var(--color-primary, #6d5ef6) 38%, var(--color-border, #e9e9ef)) !important;
  background: #fff;
  box-shadow: 0 8px 18px -16px rgba(28, 28, 38, 0.35);
  transform: translateY(-1px);
}

.add-nas-protocol-grid :deep(.add-nas-protocol-card.is-checked) {
  border-color: var(--color-primary, #6d5ef6) !important;
  background: var(--color-primary-light, #f2f0fe);
  box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary, #6d5ef6) 7%, transparent);
  color: var(--color-primary-hover, #5546d8);
}

.add-nas-protocol-card__inner {
  display: flex;
  align-items: center;
  gap: 12px;
  color: #334155;
}

.add-nas-protocol-card__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  flex: 0 0 34px;
  border-radius: 8px;
  border: 1px solid transparent;
  background: var(--color-primary-light, #f2f0fe);
  color: var(--color-primary-hover, #5546d8);
  transition: border-color 0.15s ease, background 0.15s ease, color 0.15s ease;
}

.add-nas-protocol-card:hover .add-nas-protocol-card__icon,
.add-nas-protocol-card.is-checked .add-nas-protocol-card__icon {
  border-color: var(--color-primary-disabled-bg, #dcd5fb);
  background: #fff;
  color: var(--color-primary-hover, #5546d8);
}

.add-nas-protocol-card__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.nas-add-form :deep(.fullscreen-form-section__title) {
  font-weight: 600;
}

.nas-add-form :deep(.add-nas-protocol-card__title),
.nas-add-form :deep(.add-nas-protocol-card .el-radio__label),
.nas-add-form :deep(.el-button) {
  font-weight: 400;
}

.nas-add-form :deep(.add-nas-protocol-card__title) {
  font-weight: 600;
}

.add-nas-form :deep(.el-form-item__label) {
  padding-bottom: 6px;
  color: rgb(30 41 59);
  font-weight: 400;
}

.add-nas-form-row--triple,
.add-nas-form-row--pair {
  display: grid;
  gap: 0 32px;
}

.add-nas-form-row--triple {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.add-nas-form-row--pair {
  grid-template-columns: repeat(2, minmax(0, 1fr));
  max-width: 100%;
}

.add-nas-bind-row {
  align-items: start;
}

.add-nas-bind-row__dir-hint {
  margin: 6px 0 0;
}

.add-nas-bind-row__proxy-hint {
  margin: 6px 92px 0 0;
  color: #86909c;
}

.add-nas-bind-row__proxy-hint--warn {
  color: var(--el-color-warning);
}

.nas-add-proxy-option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 2px 0;
  height: 34px;
  line-height: 1.35;
  max-width: 100%;
}

.nas-add-proxy-option__name {
  min-width: 0;
  flex: 1;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  font-weight: 500;
  color: rgb(15 23 42);
}

.add-nas-control-field {
  display: flex;
  align-items: center;
  min-height: 32px;
  width: 100%;
}

.add-nas-control-field :deep(.el-input),
.add-nas-control-field :deep(.el-select) {
  width: 100%;
}

.add-nas-form-row--triple :deep(.el-form-item),
.add-nas-form-row--pair :deep(.el-form-item) {
  margin-bottom: 0;
}

.add-nas-field-hint {
  margin: 6px 0 0;
  color: #86909c;
  font-size: 12px;
  line-height: 1.55;
}

.add-nas-field-hint--warn {
  color: #d97706;
}

.add-nas-form--auth {
  margin-top: 24px;
  padding-top: 20px;
  border-top: 1px solid rgba(226, 232, 240, 0.95);
}

.add-nas-bind-form-item :deep(.el-form-item__label) {
  display: flex;
  align-items: center;
  width: 100%;
  min-height: 32px;
  padding-bottom: 6px;
}

.add-nas-dir-form-item :deep(.el-form-item__label) {
  display: flex;
  align-items: center;
  min-height: 32px;
  padding-bottom: 6px;
}

.add-nas-select-row {
  display: flex;
  flex-direction: column;
  align-items: stretch;
  gap: 8px;
  width: 100%;
}

.add-nas-select-row__controls {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
}

.add-nas-select-row__select {
  flex: 1 1 auto;
  min-width: 0;
  width: 100%;
}

.add-nas-bind-form-item :deep(.el-form-item__content) {
  width: 100%;
}

.add-nas-select-row__refresh {
  flex: 0 0 34px;
}

.add-nas-select-row__deploy {
  flex: 0 0 40px;
}

.add-nas-select-row__select.is-error :deep(.el-select__wrapper) {
  box-shadow: 0 0 0 1px var(--el-color-danger) inset;
}

@media (max-width: 900px) {
  .add-nas-form-row--triple,
  .add-nas-form-row--pair {
    grid-template-columns: 1fr;
    gap: 0;
  }

  .add-nas-bind-row {
    grid-template-columns: minmax(0, 1fr);
  }

  .add-nas-bind-row__dir-hint {
    margin-top: 6px;
  }

  .add-nas-bind-row__proxy-hint {
    margin: 6px 0 0;
  }

  .add-nas-select-row__controls {
    flex-wrap: nowrap;
  }

  .add-nas-select-row__select {
    flex: 1 1 auto;
    min-width: 0;
  }

  .add-nas-select-row__deploy {
    flex: 0 0 40px;
  }

  .add-nas-form-row--triple :deep(.el-form-item),
  .add-nas-form-row--pair :deep(.el-form-item) {
    margin-bottom: 20px;
  }

  .add-nas-bind-row :deep(.el-form-item:last-child) {
    margin-bottom: 0;
  }
}

@media (max-width: 767.98px) {
  .add-nas-protocol-grid {
    gap: 8px;
  }

  .add-nas-protocol-grid :deep(.add-nas-protocol-card) {
    min-height: 52px;
    padding: 8px 12px !important;
  }

  .add-nas-protocol-grid :deep(.add-nas-protocol-card .el-radio__label) {
    min-width: 0;
    padding-left: 8px;
  }

  .add-nas-protocol-card__inner {
    min-width: 0;
    gap: 0;
  }

  .add-nas-protocol-card__icon {
    display: none;
  }

  .add-nas-protocol-card__title {
    font-size: 14px;
    line-height: 1.35;
  }
}
</style>

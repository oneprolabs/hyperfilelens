<script setup lang="ts">
import '../../../styles/fullscreen-form-styles'
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import NodeLifecycleWizard from '../../../components/NodeLifecycleWizard.vue'
import DangerConfirmDialog from '../../../components/DangerConfirmDialog.vue'
import { copyTextToClipboard } from '../../../lib/clipboard'
import {
  auditPlatformGatewayEnrollmentCopy,
  revokePlatformGatewayEnrollment,
} from '../../../lib/nodeApi'
import { apiErrorMessage } from '../../../lib/api'
import { routeLocationWithListRefresh } from '../../../lib/listRouteRefresh'

const { t } = useI18n()
const router = useRouter()
const wizardRef = ref<{ clearInstallCommand: () => void } | null>(null)
const tokenId = ref<number | null>(null)
const revokeOpen = ref(false)
const revoking = ref(false)

const backTarget = '/platform-ops/engine/gateways'

function onEnrollmentIssued(payload: { tokenId: number; expiresAt: string | null }) {
  tokenId.value = payload.tokenId
}

async function copyCommand(command: string) {
  try {
    await copyTextToClipboard(command)
    if (tokenId.value != null) {
      await auditPlatformGatewayEnrollmentCopy(tokenId.value).catch(() => undefined)
    }
    ElMessage.success({ message: t('nodesDeploy.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('nodesDeploy.copyFailed'), grouping: true })
  }
}

function handleBack() {
  router.push(routeLocationWithListRefresh(backTarget))
}

async function confirmRevoke() {
  if (tokenId.value == null) return
  revoking.value = true
  try {
    await revokePlatformGatewayEnrollment(tokenId.value)
    tokenId.value = null
    wizardRef.value?.clearInstallCommand()
    revokeOpen.value = false
    ElMessage.success({ message: t('platformOps.engineGateway.revokeSuccess'), grouping: true })
  } catch (error) {
    ElMessage.error({
      message: apiErrorMessage(error, t('platformOps.engineGateway.revokeFailed')),
      grouping: true,
    })
  } finally {
    revoking.value = false
  }
}

const canRevoke = computed(() => tokenId.value != null)

onMounted(() => {
  if (typeof document !== 'undefined') {
    document.body.style.overflow = 'hidden'
  }
})

onUnmounted(() => {
  if (typeof document !== 'undefined') {
    document.body.style.overflow = ''
  }
})
</script>

<template>
  <Teleport to="body">
    <div
      class="fullscreen-form-fullscreen fullscreen-form-animated resource-add-fullscreen source-deploy-fullscreen agent-deploy-fullscreen proxy-deploy-fullscreen"
      role="dialog"
      aria-modal="true"
      tabindex="-1"
      @keydown.escape.prevent="handleBack"
    >
      <div class="fullscreen-form-page source-deploy-page">
        <div class="fullscreen-form-header">
          <button
            type="button"
            class="fullscreen-form-header__back"
            @click="handleBack"
          >
            <ArrowLeft
              class="fullscreen-form-header__back-icon"
              :size="18"
            />
          </button>
          <div class="fullscreen-form-header__content">
            <h1 class="fullscreen-form-header__title">
              {{ t('nodesDeploy.pageTitlePublicGateway') }}
            </h1>
            <p class="fullscreen-form-header__desc">
              {{ t('nodesDeploy.publicGatewayIntroDesc') }}
            </p>
          </div>
        </div>

        <div class="fullscreen-form-layout">
          <div class="fullscreen-form-main">
            <NodeLifecycleWizard
              ref="wizardRef"
              install-only
              org-key="__platform_lens__"
              role="gateway"
              os="linux"
              role-locked
              gateway-scope="platform"
              @copy="copyCommand"
              @enrollment-issued="onEnrollmentIssued"
            />

            <div class="fullscreen-form-footer fullscreen-form-action-footer">
              <ElButton @click="handleBack">
                {{ t('common.back') }}
              </ElButton>
              <ElButton
                v-if="canRevoke"
                type="danger"
                plain
                @click="revokeOpen = true"
              >
                {{ t('platformOps.engineGateway.revoke') }}
              </ElButton>
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>

  <DangerConfirmDialog
    v-model="revokeOpen"
    :title="t('platformOps.engineGateway.revokeTitle')"
    :message="t('platformOps.engineGateway.revokeMessage')"
    confirm-mode="keyword"
    :confirm-keyword="t('platformOps.engineGateway.revokeKeyword')"
    :confirm-text="t('platformOps.engineGateway.revoke')"
    :cancel-text="t('common.cancel')"
    :loading="revoking"
    @confirm="confirmRevoke"
  />
</template>

<style src="../../../styles/source-deploy-ui.css"></style>
<style src="../../../styles/agent-install-wizard.css"></style>

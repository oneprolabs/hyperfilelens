<script setup lang="ts">
import '../../../styles/fullscreen-form-styles'
import { onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft } from 'lucide-vue-next'
import { ElMessage } from 'element-plus'
import NodeLifecycleWizard from '../../../components/NodeLifecycleWizard.vue'
import { copyTextToClipboard } from '../../../lib/clipboard'
import { auditPlatformGatewayEnrollmentCopy } from '../../../lib/nodeApi'
import { routeLocationWithListRefresh } from '../../../lib/listRouteRefresh'

const { t } = useI18n()
const router = useRouter()
const tokenId = ref<number | null>(null)

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
              install-only
              org-key=""
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
            </div>
          </div>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style src="../../../styles/source-deploy-ui.css"></style>
<style src="../../../styles/agent-install-wizard.css"></style>

<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft } from 'lucide-vue-next'
import NodeLifecycleWizard from '../../components/NodeLifecycleWizard.vue'
import { ElMessage } from 'element-plus'
import { getEffectiveOrgKey } from '../../composables/useAuth'
import { copyTextToClipboard } from '../../lib/clipboard'
import type { EnrollmentOs } from '../../lib/nodeApi'
import { routeLocationWithListRefresh } from '../../lib/listRouteRefresh'
import type { NodeRole } from '../../types/node'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()

const queryRole = (route.query.role as NodeRole) || 'agent'
const selectedRole = ref<NodeRole>(queryRole)
const selectedOs = ref<EnrollmentOs>('linux')
const gatewayScope = computed(() => (route.query.gatewayScope === 'platform' ? 'platform' : 'user'))

const rolePreset = computed(() => !!route.query.role)
const isProxyDeployment = computed(() => selectedRole.value === 'proxy')
const isGatewayDeployment = computed(() => selectedRole.value === 'gateway')

function normalizeReturnTo(value: unknown): string | null {
  if (typeof value !== 'string') return null
  if (!value.startsWith('/') || value.startsWith('//')) return null
  return value
}

const backToListPath = computed(() => {
  if (isProxyDeployment.value) return '/node/agents'
  if (isGatewayDeployment.value) return '/node/gateways'
  return '/protection/backup-sources?tab=host'
})

const backTarget = computed(() => normalizeReturnTo(route.query.returnTo) || backToListPath.value)

const deployPageTitle = computed(() => {
  if (isProxyDeployment.value) return t('nodesDeploy.pageTitleProxy')
  if (isGatewayDeployment.value) {
    return gatewayScope.value === 'platform'
      ? t('nodesDeploy.pageTitlePublicGateway')
      : t('nodesDeploy.pageTitlePrivateGateway')
  }
  return t('nodesDeploy.pageTitle')
})

const deployPageDesc = computed(() => {
  if (isProxyDeployment.value) return t('nodesDeploy.proxyIntroDesc')
  if (isGatewayDeployment.value) {
    return gatewayScope.value === 'platform'
      ? t('nodesDeploy.publicGatewayIntroDesc')
      : t('nodesDeploy.privateGatewayIntroDesc')
  }
  return t('nodesDeploy.enrollHint')
})

const orgKey = computed(() => (gatewayScope.value === 'platform' ? '__platform_lens__' : getEffectiveOrgKey()))

async function copyCommand(text: string) {
  if (!text) {
    ElMessage.warning({ message: t('nodesDeploy.scriptNotReady'), grouping: true })
    return
  }
  try {
    await copyTextToClipboard(text)
    ElMessage.success({ message: t('nodesDeploy.copied'), grouping: true })
  } catch {
    ElMessage.error({ message: t('nodesDeploy.copyFailed'), grouping: true })
  }
}

function pushBackWithRefresh() {
  router.push(routeLocationWithListRefresh(backTarget.value))
}

watch(
  () => route.query.role,
  (role) => {
    if (role === 'agent' || role === 'proxy' || role === 'gateway') {
      selectedRole.value = role
    }
  },
  { immediate: true },
)

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

function handleBack() {
  pushBackWithRefresh()
}
</script>

<template>
  <Teleport to="body">
    <div
      class="fullscreen-form-fullscreen fullscreen-form-animated resource-add-fullscreen source-deploy-fullscreen agent-deploy-fullscreen"
      :class="{ 'proxy-deploy-fullscreen': isProxyDeployment || isGatewayDeployment }"
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
              {{ deployPageTitle }}
            </h1>
            <p class="fullscreen-form-header__desc">
              {{ deployPageDesc }}
            </p>
          </div>
        </div>

        <div class="fullscreen-form-layout">
          <div class="fullscreen-form-main">
            <NodeLifecycleWizard
              install-only
              :org-key="orgKey"
              :role="selectedRole"
              :os="selectedOs"
              :role-locked="rolePreset"
              :show-role-picker="!rolePreset"
              :gateway-scope="gatewayScope"
              @update:os="selectedOs = $event"
              @update:role="selectedRole = $event"
              @copy="copyCommand"
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

<style src="../../styles/source-deploy-ui.css"></style>
<style src="../../styles/agent-install-wizard.css"></style>

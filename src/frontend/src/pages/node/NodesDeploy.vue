<script setup lang="ts">
import '../../styles/fullscreen-form-styles'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ArrowLeft } from 'lucide-vue-next'
import NodeLifecycleWizard from '../../components/NodeLifecycleWizard.vue'
import { ElMessage } from 'element-plus'
import { getEffectiveOrgKey } from '../../composables/useAuth'
import { useResourceCapabilities } from '../../composables/useResourceCapabilities'
import { useTenantPermissions } from '../../composables/useTenantPermissions'
import { resolveInsightEntry } from '../../lib/insightAccess'
import { resolveProtectionEntry } from '../../lib/protectionAccess'
import { copyTextToClipboard } from '../../lib/clipboard'
import type { EnrollmentOs } from '../../lib/nodeApi'
import { routeLocationWithListRefresh } from '../../lib/listRouteRefresh'
import type { NodeRole } from '../../types/node'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()

const nodeRoles: NodeRole[] = ['agent', 'proxy', 'gateway']
const queryRole = computed(() => nodeRoles.includes(route.query.role as NodeRole)
  ? route.query.role as NodeRole : 'agent')
const selectedRole = ref<NodeRole>(queryRole.value)
const selectedOs = ref<EnrollmentOs>('linux')
const gatewayScope = computed(() => selectedRole.value === 'gateway'
  && route.query.gatewayScope === 'platform' ? 'platform' : 'user')
const resourcePermissions = useResourceCapabilities(computed(() => []))
const { has } = useTenantPermissions()
const allowedRoles = computed(() => nodeRoles.filter((role) =>
  resourcePermissions.canCreate(`node:${role}`)))
const canDeploy = computed(() => gatewayScope.value !== 'platform'
  && allowedRoles.value.includes(selectedRole.value))

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

const orgKey = computed(() => (gatewayScope.value === 'platform' ? '' : getEffectiveOrgKey()))

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
  queryRole,
  (role) => {
    selectedRole.value = role
  },
  { immediate: true },
)

function selectRole(role: NodeRole) {
  if (allowedRoles.value.includes(role)) selectedRole.value = role
}

watch(
  () => [canDeploy.value, resourcePermissions.loading.value, resourcePermissions.failed.value, gatewayScope.value],
  () => {
    if (gatewayScope.value === 'platform') {
      void router.replace('/platform-ops/engine/gateways/add')
    } else if (!canDeploy.value && !resourcePermissions.loading.value && !resourcePermissions.failed.value) {
      // Do not issue enrollment while redirecting an unauthorized deep link.
      void router.replace(isGatewayDeployment.value
        ? resolveInsightEntry(has) : resolveProtectionEntry(has))
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
              v-if="canDeploy"
              install-only
              :org-key="orgKey"
              :role="selectedRole"
              :os="selectedOs"
              :role-locked="rolePreset"
              :show-role-picker="!rolePreset"
              :allowed-roles="allowedRoles"
              :gateway-scope="gatewayScope"
              @update:os="selectedOs = $event"
              @update:role="selectRole"
              @copy="copyCommand"
            />
            <ElSkeleton
              v-else-if="resourcePermissions.loading.value"
              :rows="5"
              animated
            />
            <ElAlert
              v-else-if="resourcePermissions.failed.value"
              type="error"
              :closable="false"
              :title="t('errors.generic.loadFailed')"
            >
              <ElButton @click="resourcePermissions.refresh()">
                {{ t('common.retry') }}
              </ElButton>
            </ElAlert>

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

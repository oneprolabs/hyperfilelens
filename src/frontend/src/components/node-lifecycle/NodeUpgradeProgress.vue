<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertTriangle, ArrowRight, Check, Circle, Clock3, X } from 'lucide-vue-next'
import type { NodeLifecycleInfo } from '../../types/nodeLifecycle'

const props = defineProps<{
  lifecycle: NodeLifecycleInfo | null
}>()

const { locale, t } = useI18n()

const hasLifecycle = computed(() => {
  return (
    props.lifecycle != null &&
    props.lifecycle.kind === 'upgrade' &&
    props.lifecycle.timeline != null &&
    props.lifecycle.timeline.length > 0
  )
})

const timeline = computed(() => props.lifecycle?.timeline ?? [])

function phaseIcon(status: string) {
  switch (status) {
    case 'completed':
      return 'success'
    case 'active':
      return 'running'
    case 'failed':
      return 'danger'
    default:
      return 'muted'
  }
}

function formatPhaseTime(at: string | null) {
  if (!at) return '—'
  const date = new Date(at)
  if (Number.isNaN(date.getTime())) return at
  return date.toLocaleString(locale.value)
}
</script>

<template>
  <div
    v-if="hasLifecycle"
    class="node-upgrade-progress"
  >
    <div class="node-upgrade-progress__hero">
      <div class="node-upgrade-progress__hero-section-title">
        {{ t('nodeUpgradeProgress.title') }}
      </div>
      <div class="node-upgrade-progress__version-row">
        <span class="node-upgrade-progress__version">{{ lifecycle?.current_version || '—' }}</span>
        <ArrowRight
          :size="14"
          class="node-upgrade-progress__arrow"
        />
        <span class="node-upgrade-progress__version node-upgrade-progress__version--target">{{ lifecycle?.target_version || '—' }}</span>
      </div>
    </div>

    <div class="node-upgrade-progress__step-list">
      <div
        v-for="(phase, index) in timeline"
        :key="phase.phase"
        class="node-upgrade-progress__step-item"
        :class="{
          'node-upgrade-progress__step-item--last': index === timeline.length - 1,
        }"
      >
        <div
          class="node-upgrade-progress__step-anchor"
          :class="`node-upgrade-progress__step-anchor--${phaseIcon(phase.status)}`"
        >
          <Check
            v-if="phase.status === 'completed'"
            :size="15"
          />
          <Clock3
            v-else-if="phase.status === 'active'"
            :size="15"
          />
          <X
            v-else-if="phase.status === 'failed'"
            :size="15"
          />
          <Circle
            v-else
            :size="9"
          />
        </div>

        <article class="node-upgrade-progress__step-card">
          <div class="node-upgrade-progress__step-card-head">
            <span class="node-upgrade-progress__step-title">
              {{ phase.label }}
              <span
                v-if="phase.at"
                class="node-upgrade-progress__step-time"
              >{{ formatPhaseTime(phase.at) }}</span>
            </span>
            <span
              class="node-upgrade-progress__step-tag"
              :class="`node-upgrade-progress__step-tag--${phase.status}`"
            >
              <template v-if="phase.status === 'completed'">{{ t('nodeUpgradeProgress.completed') }}</template>
              <template v-else-if="phase.status === 'active'">{{ t('nodeUpgradeProgress.inProgress') }}</template>
              <template v-else-if="phase.status === 'failed'">{{ t('nodeUpgradeProgress.failed') }}</template>
              <template v-else>{{ t('nodeUpgradeProgress.pending') }}</template>
            </span>
          </div>
          <div
            v-if="phase.error"
            class="node-upgrade-progress__step-error"
          >
            <AlertTriangle
              :size="13"
              aria-hidden="true"
            />
            <span>{{ phase.error }}</span>
          </div>
        </article>
      </div>
    </div>

    <div
      v-if="lifecycle?.error"
      class="node-upgrade-progress__error"
    >
      <AlertTriangle
        :size="15"
        aria-hidden="true"
      />
      <span>{{ lifecycle.error }}</span>
    </div>
  </div>
  <div
    v-else
    class="node-upgrade-progress__empty"
  >
    {{ t('nodeUpgradeProgress.noActiveUpgrade') }}
  </div>
</template>

<style scoped>
.node-upgrade-progress {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.node-upgrade-progress__empty {
  color: rgb(148 163 184);
  font-size: 13px;
  padding: 20px 0;
  text-align: center;
}

/* --- hero / version header --- */
.node-upgrade-progress__hero {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 16px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  background: rgb(248 250 252);
  box-shadow: inset 0 1px 1px rgba(15, 23, 42, 0.03);
}

.node-upgrade-progress__hero-section-title {
  font-size: 12px;
  font-weight: 800;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  color: rgb(100 116 139);
}

.node-upgrade-progress__version-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.node-upgrade-progress__version {
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 14px;
  font-weight: 700;
  color: rgb(51 65 85);
}

.node-upgrade-progress__version--target {
  color: var(--color-info);
}

.node-upgrade-progress__arrow {
  color: rgb(148 163 184);
  flex-shrink: 0;
}

/* --- step list timeline --- */
.node-upgrade-progress__step-list {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.node-upgrade-progress__step-item {
  position: relative;
  display: grid;
  grid-template-columns: 36px minmax(0, 1fr);
  gap: 12px;
}

.node-upgrade-progress__step-item::before {
  content: '';
  position: absolute;
  left: 12px;
  top: 34px;
  bottom: -18px;
  width: 0;
  border-left: 2px dashed rgb(226 232 240);
}

.node-upgrade-progress__step-item--last::before {
  display: none;
}

/* --- step anchor / dot --- */
.node-upgrade-progress__step-anchor {
  position: relative;
  z-index: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 26px;
  height: 26px;
  margin-top: 1px;
  border: 2px solid #fff;
  border-radius: 999px;
}

.node-upgrade-progress__step-anchor--success {
  border-color: var(--color-success);
  background-color: var(--color-success);
  color: #fff;
}

.node-upgrade-progress__step-anchor--running {
  border-color: var(--color-info);
  background-color: var(--color-info);
  color: #fff;
}

.node-upgrade-progress__step-anchor--danger {
  border-color: var(--color-error);
  background-color: var(--color-error);
  color: #fff;
}

.node-upgrade-progress__step-anchor--muted {
  background-color: rgb(100 116 139);
  color: #fff;
}

/* --- step card --- */
.node-upgrade-progress__step-card {
  min-width: 0;
  padding: 14px 16px;
  border: 1px solid var(--color-border, #e2e8f0);
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.05);
}

.node-upgrade-progress__step-card-head {
  display: flex;
  align-items: center;
  gap: 10px;
  width: 100%;
  min-width: 0;
}

.node-upgrade-progress__step-title {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  font-weight: 800;
  line-height: 1.45;
  color: rgb(15 23 42);
  overflow-wrap: anywhere;
}

.node-upgrade-progress__step-time {
  display: inline-flex;
  margin-left: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', monospace;
  font-size: 11px;
  font-weight: 700;
  color: rgb(71 85 105);
}

/* --- step status tag --- */
.node-upgrade-progress__step-tag {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 6px;
  font-size: 11px;
  font-weight: 700;
}

.node-upgrade-progress__step-tag--completed {
  background: var(--color-success-light);
  color: var(--color-success);
}

.node-upgrade-progress__step-tag--active {
  background: var(--color-info-light);
  color: var(--color-info);
}

.node-upgrade-progress__step-tag--failed {
  background: var(--color-error-light);
  color: var(--color-error);
}

.node-upgrade-progress__step-tag--pending {
  background: rgb(241 245 249);
  color: rgb(148 163 184);
}

/* --- step error --- */
.node-upgrade-progress__step-error {
  display: flex;
  align-items: flex-start;
  gap: 6px;
  margin-top: 10px;
  padding-top: 10px;
  border-top: 1px solid rgb(241 245 249);
  font-size: 12px;
  color: var(--color-error);
  line-height: 1.45;
  overflow-wrap: anywhere;
}

.node-upgrade-progress__step-error svg {
  flex-shrink: 0;
  margin-top: 2px;
}

/* --- global lifecycle error --- */
.node-upgrade-progress__error {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  padding: 12px 14px;
  border: 1px solid rgb(254 202 202);
  border-radius: 10px;
  background: rgb(254 242 242);
  color: rgb(185 28 28);
  font-size: 13px;
  line-height: 1.5;
}

.node-upgrade-progress__error svg {
  flex-shrink: 0;
  margin-top: 2px;
}
</style>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

const props = defineProps<{ metadata?: unknown }>()
const { t } = useI18n()

const identities = computed(() => {
  if (!props.metadata || typeof props.metadata !== 'object') return null
  const value = (props.metadata as Record<string, unknown>).impersonation
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  const info = value as Record<string, unknown>
  const label = (email: unknown, id: unknown): string => {
    if (typeof email === 'string' && email.trim()) return email.trim()
    return typeof id === 'string' || typeof id === 'number' ? String(id) : ''
  }
  const actor = label(info.actor_email, info.actor_id)
  const subject = label(info.subject_email, info.subject_id)
  return actor && subject ? { actor, subject } : null
})
</script>

<template>
  <div
    v-if="identities"
    class="impersonation-attribution"
  >
    <span>{{ t('impersonation.performedBy') }}: <strong>{{ identities.actor }}</strong></span>
    <span>{{ t('impersonation.actedAs') }}: <strong>{{ identities.subject }}</strong></span>
  </div>
</template>

<style scoped>
.impersonation-attribution {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 16px;
  margin-top: 4px;
  color: var(--text-secondary);
  font-size: 12px;
  line-height: 1.5;
  overflow-wrap: anywhere;
}
.impersonation-attribution strong { font-weight: 500; }
</style>

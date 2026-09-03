<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertCircle, Ellipsis, Pencil, Pin, PinOff, Plus, RotateCcw, Share2, Trash2 } from 'lucide-vue-next'
import { isActiveRunStatus } from '../../../composables/useLensRunStream'
import type { SessionGroupKey, SessionRow } from './sessionOrdering'

const props = defineProps<{
  sessions: SessionRow[]
  activeId: number | null
  loading?: boolean
  pendingNotifications?: Set<number>
}>()

const emit = defineEmits<{
  select: [id: number]
  share: [row: SessionRow]
  delete: [row: SessionRow]
  rename: [row: SessionRow, title: string]
  retry: [row: SessionRow]
  pin: [row: SessionRow, pinned: boolean]
  newChat: []
}>()

const { t } = useI18n()
const editingId = ref<number | null>(null)
const editingTitle = ref('')
const skipBlurCommit = ref(false)

const sessionGroups = computed(() => {
  const keys: SessionGroupKey[] = ['pinned', 'today', 'yesterday', 'earlier']
  const labels: Record<SessionGroupKey, string> = {
    pinned: t('insight.copilot.groupPinned'),
    today: t('insight.copilot.groupToday'),
    yesterday: t('insight.copilot.groupYesterday'),
    earlier: t('insight.copilot.groupEarlier'),
  }
  return keys.map((key) => ({
    key,
    label: labels[key],
    rows: props.sessions.filter((row) => row.group === key),
  })).filter((group) => group.rows.length > 0)
})

function sessionTitle(row: SessionRow) {
  return row.title?.trim() || row.backup_source_name || t('insight.copilot.newChatTitle')
}

function sessionMeta(row: SessionRow) {
  if (sessionIsRecovering(row)) return t('insight.copilot.sessionRecovering')
  if (sessionCleanupBlocked(row)) return t('insight.copilot.sessionRecoveryAttention')
  if (row.lifecycle_status === 'provisioning') return t('insight.copilot.sessionPreparing')
  if (row.lifecycle_status === 'failed') return t('insight.copilot.sessionPreparationFailed')
  if (row.lifecycle_status === 'deleting') return t('insight.copilot.sessionDeleting')
  if (sessionIsRunning(row)) return t('insight.copilot.sessionAnswering')
  const source = row.backup_source_name?.trim() || t('insight.copilot.backupSourceFallback')
  const scopes = row.source_scopes_json || []
  if (!scopes.length) return source
  const types = scopes.map((scope) => scope.path_type || 'unknown')
  const allFiles = types.every((type) => type === 'file')
  const allFolders = types.every((type) => type === 'dir')
  const plurality = scopes.length === 1 ? 'One' : 'Many'
  let scopeLabel = t(`insight.copilot.scopeItems${plurality}`, { count: scopes.length })
  if (allFiles) scopeLabel = t(`insight.copilot.scopeFiles${plurality}`, { count: scopes.length })
  if (allFolders) scopeLabel = t(`insight.copilot.scopeFolders${plurality}`, { count: scopes.length })
  return `${source} · ${scopeLabel}`
}

function sessionIsRecovering(row: SessionRow) {
  return row.lifecycle_status === 'failed'
    && row.cleanup_intent === 'reset_for_retry'
    && ['pending', 'running'].includes(row.cleanup_status || '')
}

function sessionCleanupBlocked(row: SessionRow) {
  return row.lifecycle_status === 'failed'
    && row.cleanup_intent === 'reset_for_retry'
    && row.cleanup_status === 'blocked'
}

function sessionIsRetryable(row: SessionRow) {
  return row.lifecycle_status === 'failed'
    && !['pending', 'running', 'blocked'].includes(row.cleanup_status || '')
}

function sessionIsRunning(row: SessionRow) {
  return isActiveRunStatus(row.active_run_status)
}

function sessionHasUnread(row: SessionRow) {
  return row.has_unread || (props.pendingNotifications?.has(row.id) ?? false)
}

function sessionHasShareableAnswer(row: SessionRow) {
  return row.has_shareable_answer ?? Boolean(row.last_assistant_message_at)
}

function startRename(row: SessionRow) {
  editingId.value = row.id
  editingTitle.value = sessionTitle(row)
  nextTick(() => {
    const input = document.querySelector('.copilot-session-item.is-editing .copilot-session-item__input') as HTMLInputElement | null
    input?.focus()
    input?.select()
  })
}

function cancelRename() {
  editingId.value = null
  editingTitle.value = ''
}

function commitRename(row: SessionRow, fromBlur = false) {
  if (fromBlur && skipBlurCommit.value) {
    skipBlurCommit.value = false
    return
  }
  const title = editingTitle.value.trim()
  if (title && title !== sessionTitle(row)) emit('rename', row, title)
  cancelRename()
}

function onRenameKeydown(event: KeyboardEvent, row: SessionRow) {
  if (event.key === 'Enter') {
    event.preventDefault()
    skipBlurCommit.value = true
    commitRename(row)
  } else if (event.key === 'Escape') {
    event.preventDefault()
    skipBlurCommit.value = true
    cancelRename()
  }
}

function handleAction(command: string, row: SessionRow) {
  if (command === 'share') emit('share', row)
  if (command === 'pin') emit('pin', row, true)
  if (command === 'unpin') emit('pin', row, false)
  if (command === 'rename') startRename(row)
  if (command === 'retry') emit('retry', row)
  if (command === 'delete') emit('delete', row)
}
</script>

<template>
  <aside class="copilot-aside">
    <div class="copilot-aside__new">
      <ElButton
        class="w-full !justify-center"
        type="primary"
        size="small"
        @click="emit('newChat')"
      >
        <Plus :size="16" />{{ t('insight.copilot.newChat') }}
      </ElButton>
    </div>

    <div
      v-loading="loading"
      class="copilot-session-scroll"
    >
      <template
        v-for="group in sessionGroups"
        :key="group.key"
      >
        <div class="copilot-session-group">
          {{ group.label }}
        </div>
        <article
          v-for="row in group.rows"
          :key="row.id"
          class="copilot-session-item"
          :class="{ 'is-active': activeId === row.id, 'is-editing': editingId === row.id }"
          @click="editingId === row.id ? undefined : emit('select', row.id)"
        >
          <div class="copilot-session-item__content">
            <div class="copilot-session-item__title-row">
              <input
                v-if="editingId === row.id"
                v-model="editingTitle"
                class="copilot-session-item__input"
                maxlength="160"
                :placeholder="t('insight.copilot.renamePlaceholder')"
                @click.stop
                @keydown="onRenameKeydown($event, row)"
                @blur="commitRename(row, true)"
              >
              <Pin
                v-if="editingId !== row.id && row.pinned_at"
                class="copilot-session-item__pin"
                :size="13"
                :aria-label="t('insight.copilot.pinned')"
              />
              <span
                v-if="editingId !== row.id"
                class="copilot-session-item__title"
                :title="sessionTitle(row)"
              >{{ sessionTitle(row) }}</span>
            </div>
            <span
              class="copilot-session-item__meta"
              :title="sessionMeta(row)"
            >{{ sessionMeta(row) }}</span>
          </div>

          <div
            v-if="editingId !== row.id"
            class="copilot-session-item__trailing"
          >
            <span
              class="copilot-session-item__state-slot"
              aria-hidden="true"
            >
              <span
                v-if="sessionCleanupBlocked(row)"
                class="copilot-session-item__state is-failed"
                :title="t('insight.copilot.sessionRecoveryAttention')"
              ><AlertCircle :size="14" /></span>
              <span
                v-else-if="row.lifecycle_status === 'provisioning' || sessionIsRecovering(row)"
                class="copilot-session-item__state is-preparing"
                :title="sessionIsRecovering(row) ? t('insight.copilot.sessionRecovering') : t('insight.copilot.sessionPreparing')"
              ><i /><i /><i /></span>
              <span
                v-else-if="row.lifecycle_status === 'failed'"
                class="copilot-session-item__state is-failed"
                :title="t('insight.copilot.sessionPreparationFailed')"
              ><AlertCircle :size="14" /></span>
              <span
                v-else-if="sessionIsRunning(row)"
                class="copilot-session-item__state is-running"
                :title="t('insight.copilot.sessionAnswering')"
              />
              <span
                v-else-if="sessionHasUnread(row)"
                class="copilot-session-item__state is-unread"
                :title="t('insight.copilot.unreadAnswer')"
              />
            </span>
            <div class="copilot-session-item__actions">
              <ElDropdown
                trigger="click"
                popper-class="hfl-actions-dropdown copilot-session-menu"
                @command="(command) => handleAction(String(command), row)"
              >
                <button
                  class="copilot-session-item__more"
                  type="button"
                  :aria-label="t('insight.copilot.sessionActions', { title: sessionTitle(row) })"
                  @click.stop
                >
                  <Ellipsis :size="17" />
                </button>
                <template #dropdown>
                  <ElDropdownMenu>
                    <ElDropdownItem
                      v-if="row.lifecycle_status === 'ready'"
                      class="copilot-session-menu__share"
                      command="share"
                      :icon="Share2"
                      :disabled="!sessionHasShareableAnswer(row)"
                      :title="!sessionHasShareableAnswer(row) ? t('insight.copilot.shareUnavailable') : ''"
                    >
                      {{ t('insight.copilot.share') }}
                    </ElDropdownItem>
                    <ElDropdownItem
                      class="copilot-session-menu__rename"
                      command="rename"
                      :icon="Pencil"
                    >
                      {{ t('insight.copilot.renameSession') }}
                    </ElDropdownItem>
                    <ElDropdownItem
                      v-if="row.lifecycle_status === 'ready'"
                      class="copilot-session-menu__pin"
                      :command="row.pinned_at ? 'unpin' : 'pin'"
                      :icon="row.pinned_at ? PinOff : Pin"
                    >
                      {{ row.pinned_at ? t('insight.copilot.unpinSession') : t('insight.copilot.pinSession') }}
                    </ElDropdownItem>
                    <ElDropdownItem
                      v-if="sessionIsRetryable(row)"
                      class="copilot-session-menu__retry"
                      command="retry"
                      :icon="RotateCcw"
                    >
                      {{ t('insight.copilot.tryAgain') }}
                    </ElDropdownItem>
                    <ElDropdownItem
                      class="copilot-session-menu__delete el-dropdown-menu__item--danger"
                      command="delete"
                      :icon="Trash2"
                      divided
                    >
                      {{ t('insight.copilot.deleteSession') }}
                    </ElDropdownItem>
                  </ElDropdownMenu>
                </template>
              </ElDropdown>
            </div>
          </div>
        </article>
      </template>

      <div
        v-if="!loading && !sessions.length"
        class="copilot-session-empty"
      >
        {{ t('insight.copilot.emptyNoSessions') }}
      </div>
    </div>
  </aside>
</template>

<style scoped>
.copilot-aside { display: flex; width: 280px; min-height: 0; flex-shrink: 0; flex-direction: column; border-right: 1px solid var(--color-border); background: var(--color-grey-2); }
.copilot-aside__new { padding: 12px; border-bottom: 1px solid var(--color-border); }
.copilot-session-scroll { min-height: 0; flex: 1; padding: 8px; overflow-y: auto; }
.copilot-session-group { padding: 7px 10px 5px; color: var(--color-text-tertiary); font-size: 11px; font-weight: 600; }
.copilot-session-item { position: relative; display: grid; min-height: 58px; grid-template-columns: minmax(0, 1fr) 28px; align-items: center; column-gap: 6px; margin: 0; padding: 9px 8px 9px 12px; overflow: hidden; border-radius: 8px; color: var(--color-text-title); cursor: pointer; transition: background .15s ease, box-shadow .15s ease; }
.copilot-session-item::after { position: absolute; right: 8px; bottom: 0; left: 12px; height: 1px; background: color-mix(in srgb, var(--color-border) 82%, transparent); content: ''; pointer-events: none; }
.copilot-session-item:hover { background: color-mix(in srgb, var(--color-primary) 4%, var(--color-card-bg)); }
.copilot-session-item:hover::after,.copilot-session-item.is-active::after { opacity: 0; }
.copilot-session-item.is-active { background: color-mix(in srgb, var(--color-primary) 8%, var(--color-card-bg)); box-shadow: inset 2px 0 0 var(--color-primary); }
.copilot-session-item.is-editing { cursor: default; }
.copilot-session-item.is-editing .copilot-session-item__content { grid-column: 1 / -1; }
.copilot-session-item__content { display: grid; min-width: 0; gap: 4px; }
.copilot-session-item__title-row { display: flex; min-width: 0; align-items: center; gap: 7px; }
.copilot-session-item__pin { flex: 0 0 auto; color: var(--color-primary); }
.copilot-session-item__title { min-width: 0; flex: 1; overflow: hidden; color: var(--color-text-title); font-size: 13px; font-weight: 600; line-height: 18px; text-overflow: ellipsis; white-space: nowrap; }
.copilot-session-item__meta { overflow: hidden; color: var(--color-text-tertiary); font-size: 11px; line-height: 16px; text-overflow: ellipsis; white-space: nowrap; }
.copilot-session-item__input { width: 100%; min-width: 0; padding: 2px 6px; border: 1px solid var(--color-primary-light); border-radius: 6px; outline: none; background: var(--color-card-bg); color: var(--color-text-title); font: inherit; font-size: 13px; font-weight: 600; }
.copilot-session-item__trailing { position: relative; width: 28px; height: 20px; align-self: start; }
.copilot-session-item__state-slot { position: absolute; inset: 0; display: inline-flex; align-items: center; justify-content: center; opacity: 1; transition: opacity .15s ease; }
.copilot-session-item__state { display: inline-flex; flex-shrink: 0; align-items: center; justify-content: center; }
.copilot-session-item__state.is-failed { color: #d92d20; }
.copilot-session-item__state.is-running { width: 14px; height: 14px; border: 2px solid color-mix(in srgb, var(--color-primary) 24%, transparent); border-top-color: var(--color-primary); border-radius: 999px; animation: copilot-sidebar-spin .8s linear infinite; transform-origin: center; }
.copilot-session-item__state.is-unread { width: 8px; height: 8px; border-radius: 999px; background: var(--color-primary); box-shadow: 0 0 0 3px color-mix(in srgb, var(--color-primary) 12%, transparent); }
.copilot-session-item__state.is-preparing { gap: 2px; }
.copilot-session-item__state.is-preparing i { width: 4px; height: 4px; border-radius: 999px; background: var(--color-primary); animation: copilot-dot 1.2s ease-in-out infinite; }
.copilot-session-item__state.is-preparing i:nth-child(2) { animation-delay: .15s; }.copilot-session-item__state.is-preparing i:nth-child(3) { animation-delay: .3s; }
.copilot-session-item__actions { position: absolute; top: -4px; right: 0; z-index: 1; width: 28px; height: 28px; opacity: 0; pointer-events: none; transition: opacity .15s ease; }
.copilot-session-item__more { display: inline-flex; width: 28px; height: 28px; flex-shrink: 0; align-items: center; justify-content: center; padding: 0; border: 0; border-radius: 7px; background: transparent; color: var(--color-text-tertiary); cursor: pointer; opacity: 0; transition: opacity .15s ease, background .15s ease; }
.copilot-session-item:hover .copilot-session-item__state-slot,.copilot-session-item:focus-within .copilot-session-item__state-slot { opacity: 0; }
.copilot-session-item:hover .copilot-session-item__actions,.copilot-session-item:focus-within .copilot-session-item__actions { opacity: 1; pointer-events: auto; }
.copilot-session-item:hover .copilot-session-item__more,.copilot-session-item__more:focus-visible { opacity: 1; }
.copilot-session-item__more:hover { background: color-mix(in srgb, var(--color-text-title) 8%, transparent); color: var(--color-text-title); }
:global(.copilot-session-menu__share),:global(.copilot-session-menu__pin),:global(.copilot-session-menu__rename),:global(.copilot-session-menu__retry),:global(.copilot-session-menu__delete) { min-height: 36px; padding: 0 14px; font-size: 13px; }
:global(.copilot-session-menu__share .el-icon),:global(.copilot-session-menu__pin .el-icon),:global(.copilot-session-menu__rename .el-icon),:global(.copilot-session-menu__retry .el-icon),:global(.copilot-session-menu__delete .el-icon) { width: 15px; height: 15px; margin-right: 9px; font-size: 15px; }
:global(.copilot-session-menu__share:not(.is-disabled):hover),
:global(.copilot-session-menu__pin:not(.is-disabled):hover),
:global(.copilot-session-menu__rename:not(.is-disabled):hover) { color: var(--color-primary) !important; }
:global(.copilot-session-menu__delete) { color: var(--color-error-text) !important; }
:global(.copilot-session-menu__delete:hover) { background: var(--color-error-light) !important; color: var(--color-error-text) !important; }
.copilot-session-empty { padding: 24px 8px; color: var(--color-text-tertiary); font-size: 12px; text-align: center; }
@keyframes copilot-sidebar-spin { to { transform: rotate(360deg); } }
@keyframes copilot-dot { 0%,60%,100% { opacity: .28; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-2px); } }
@media (max-width: 900px) { .copilot-aside { width: 240px; } }
@media (hover: none), (pointer: coarse) {
  .copilot-session-item__actions { right: -8px; width: 44px; height: 44px; opacity: 1; pointer-events: auto; }
  .copilot-session-item__more { width: 44px; height: 44px; opacity: 1; }
  .copilot-session-item__state-slot { right: 36px; }
  :global(.copilot-session-menu__share),:global(.copilot-session-menu__pin),:global(.copilot-session-menu__rename),:global(.copilot-session-menu__retry),:global(.copilot-session-menu__delete) { min-height: 44px; }
}
</style>

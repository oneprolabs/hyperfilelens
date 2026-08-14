<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { AlertCircle, Ellipsis, Pencil, Plus, RotateCcw, Trash2 } from 'lucide-vue-next'
import { isActiveRunStatus } from '../../../composables/useLensRunStream'
import type { LensSessionLink } from '../../../lib/lensApi'

export type SessionGroupKey = 'today' | 'yesterday' | 'earlier'
export type SessionRow = LensSessionLink & { group: SessionGroupKey }

const props = defineProps<{
  sessions: SessionRow[]
  activeId: number | null
  loading?: boolean
  pendingNotifications?: Set<number>
}>()

const emit = defineEmits<{
  select: [id: number]
  delete: [row: SessionRow]
  rename: [row: SessionRow, title: string]
  retry: [row: SessionRow]
  newChat: []
}>()

const { t } = useI18n()
const editingId = ref<number | null>(null)
const editingTitle = ref('')
const skipBlurCommit = ref(false)

const sessionGroups = computed(() => {
  const keys: SessionGroupKey[] = ['today', 'yesterday', 'earlier']
  const labels: Record<SessionGroupKey, string> = {
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
  if (row.lifecycle_status === 'provisioning') return 'Preparing Chat…'
  if (row.lifecycle_status === 'failed') return 'Preparation Failed'
  if (row.lifecycle_status === 'deleting') return 'Deleting…'
  if (sessionIsRunning(row)) return 'Answering…'
  const source = row.backup_source_name?.trim() || 'Backup Source'
  const scopes = row.source_scopes_json || []
  if (!scopes.length) return source
  const types = scopes.map((scope) => scope.path_type || 'unknown')
  const allFiles = types.every((type) => type === 'file')
  const allFolders = types.every((type) => type === 'dir')
  let scopeLabel = `${scopes.length} Item${scopes.length === 1 ? '' : 's'}`
  if (allFiles) scopeLabel = `${scopes.length} File${scopes.length === 1 ? '' : 's'}`
  if (allFolders) scopeLabel = `${scopes.length} Folder${scopes.length === 1 ? '' : 's'}`
  return `${source} · ${scopeLabel}`
}

function sessionIsRunning(row: SessionRow) {
  return isActiveRunStatus(row.active_run_status)
}

function sessionHasUnread(row: SessionRow) {
  return row.has_unread || (props.pendingNotifications?.has(row.id) ?? false)
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
              <span
                v-else
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
                v-if="row.lifecycle_status === 'failed'"
                class="copilot-session-item__state is-failed"
                title="Preparation failed"
              ><AlertCircle :size="14" /></span>
              <span
                v-else-if="row.lifecycle_status === 'provisioning'"
                class="copilot-session-item__state is-preparing"
                title="Preparing"
              ><i /><i /><i /></span>
              <span
                v-else-if="sessionIsRunning(row)"
                class="copilot-session-item__state is-running"
                title="Answering"
              />
              <span
                v-else-if="sessionHasUnread(row)"
                class="copilot-session-item__state is-unread"
                title="New answer"
              />
            </span>
            <div class="copilot-session-item__actions">
              <ElDropdown
                trigger="click"
                @command="(command) => handleAction(String(command), row)"
              >
                <button
                  class="copilot-session-item__more"
                  type="button"
                  aria-label="Chat actions"
                  @click.stop
                >
                  <Ellipsis :size="17" />
                </button>
                <template #dropdown>
                  <ElDropdownMenu>
                    <ElDropdownItem
                      class="copilot-session-menu__rename"
                      command="rename"
                      :icon="Pencil"
                    >
                      Rename Chat
                    </ElDropdownItem>
                    <ElDropdownItem
                      v-if="row.lifecycle_status === 'failed'"
                      class="copilot-session-menu__retry"
                      command="retry"
                      :icon="RotateCcw"
                    >
                      Try Again
                    </ElDropdownItem>
                    <ElDropdownItem
                      class="copilot-session-menu__delete"
                      command="delete"
                      :icon="Trash2"
                      divided
                    >
                      Delete Chat
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
:global(.copilot-session-menu__rename),:global(.copilot-session-menu__retry),:global(.copilot-session-menu__delete) { min-height: 36px; padding: 0 14px; font-size: 13px; }
:global(.copilot-session-menu__rename .el-icon),:global(.copilot-session-menu__retry .el-icon),:global(.copilot-session-menu__delete .el-icon) { width: 15px; height: 15px; margin-right: 9px; font-size: 15px; }
:global(.copilot-session-menu__rename:hover) { color: var(--color-primary) !important; }
:global(.copilot-session-menu__delete) { color: #d92d20 !important; }
:global(.copilot-session-menu__delete:hover) { background: #fef3f2 !important; color: #b42318 !important; }
.copilot-session-empty { padding: 24px 8px; color: var(--color-text-tertiary); font-size: 12px; text-align: center; }
@keyframes copilot-sidebar-spin { to { transform: rotate(360deg); } }
@keyframes copilot-dot { 0%,60%,100% { opacity: .28; transform: translateY(0); } 30% { opacity: 1; transform: translateY(-2px); } }
@media (max-width: 900px) { .copilot-aside { width: 240px; } }
@media (hover: none), (pointer: coarse) {
  .copilot-session-item__actions { opacity: 1; pointer-events: auto; }
  .copilot-session-item__more { opacity: 1; }
  .copilot-session-item__state-slot { right: 30px; }
}
</style>

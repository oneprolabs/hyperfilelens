import { computed, onBeforeUnmount, type Ref } from 'vue'
import { getTask, type TaskRow } from '../lib/taskApi'
import {
  createRepositoryTaskCancellationPoller,
  isRepositoryTaskCancellationPending,
  isTerminalTaskStatus,
} from '../lib/repositoryTaskCancellation'

type Options = {
  onUpdate: (task: TaskRow) => void | Promise<void>
  onTerminal?: (task: TaskRow) => void | Promise<void>
  onError?: (error: unknown) => void
  fetchTask?: (taskUuid: string, init?: RequestInit) => Promise<TaskRow>
}

export function useRepositoryTaskCancellation(
  activeTask: Ref<TaskRow | null>,
  options: Options,
) {
  const pending = computed(() => isRepositoryTaskCancellationPending(activeTask.value))
  const poller = createRepositoryTaskCancellationPoller({
    fetchTask: options.fetchTask || getTask,
    onUpdate: async (task) => {
      if (activeTask.value?.task_uuid !== task.task_uuid) return
      await options.onUpdate(task)
      if (isTerminalTaskStatus(task.status)) await options.onTerminal?.(task)
    },
    onError: options.onError,
  })

  function stop() {
    poller.stop()
  }

  function sync(task: TaskRow | null = activeTask.value): boolean {
    stop()
    if (!isRepositoryTaskCancellationPending(task)) return false
    poller.start(task!.task_uuid)
    return true
  }

  onBeforeUnmount(stop)
  return { pending, stop, sync }
}

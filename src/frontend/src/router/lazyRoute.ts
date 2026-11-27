import { defineAsyncComponent, defineComponent, h, type Component } from 'vue'
import { ChunkLoadError } from './ChunkLoadError'
import { withRequestDeadline } from '../lib/requestDeadline'
import {
  clearChunkLoadFailureAttempt,
  isDynamicImportFailure,
  reloadOnceForChunkLoadFailure,
} from './chunkLoadRecovery'

type RouteLoader = () => Promise<{ default: Component }>

export function lazyRoute(loader: RouteLoader, timeoutMs?: number): Component {
  const AsyncPage = defineAsyncComponent({
    loader: timeoutMs ? () => withRequestDeadline(loader, undefined, timeoutMs) : loader,
    suspensible: true,
    errorComponent: ChunkLoadError,
    onError(error, _retry, fail) {
      if (isDynamicImportFailure(error) && reloadOnceForChunkLoadFailure()) return
      fail()
    },
  })

  return defineComponent({
    name: 'LazyRouteView',
    setup() {
      return () => h(AsyncPage, {
        onVnodeMounted: () => clearChunkLoadFailureAttempt(),
      })
    },
  })
}

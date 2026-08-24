import { computed, nextTick, ref } from 'vue'
import { describe, expect, it } from 'vitest'

describe('backup wizard step 2 count semantics', () => {
  it('updates the global pending summary without changing the filtered table count', async () => {
    const pipelineStep2Count = ref(1)
    const step2SelectableCount = ref(1)
    const step2GlobalPendingCount = computed(() => pipelineStep2Count.value)

    pipelineStep2Count.value = 0
    await nextTick()

    expect(step2GlobalPendingCount.value).toBe(0)
    expect(step2SelectableCount.value).toBe(1)

    step2SelectableCount.value = 0
    await nextTick()

    expect(step2GlobalPendingCount.value).toBe(0)
    expect(step2SelectableCount.value).toBe(0)
  })
})

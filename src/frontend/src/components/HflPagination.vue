<script setup lang="ts">
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { ElPagination } from 'element-plus'
import { useResponsiveLayout } from '../composables/useResponsiveLayout'

defineOptions({
  name: 'HflPagination',
  inheritAttrs: false,
})

const props = withDefaults(
  defineProps<{
    currentPage?: number
    pageSize?: number
    total?: number
    pageSizes?: number[]
    layout?: string
    size?: 'large' | 'default' | 'small'
    popperClass?: string
    pagerCount?: number
    responsive?: boolean
  }>(),
  {
    currentPage: 1,
    pageSize: 10,
    total: 0,
    pageSizes: () => [10, 20, 30, 50, 100],
    layout: 'total, sizes, prev, pager, next, jumper',
    size: 'small',
    popperClass: '',
    pagerCount: undefined,
    responsive: true,
  },
)

const emit = defineEmits<{
  'update:currentPage': [value: number]
  'update:pageSize': [value: number]
}>()

const mergedPopperClass = computed(() =>
  ['hfl-pagination-size-popper', props.popperClass].filter(Boolean).join(' '),
)
const paginationRef = ref<{ $el?: HTMLElement } | null>(null)
const { isPhone } = useResponsiveLayout()
const resolvedLayout = computed(() =>
  props.responsive && isPhone.value
    ? 'prev, pager, next'
    : props.layout ?? 'total, sizes, prev, pager, next, jumper',
)
const resolvedPagerCount = computed(() => props.pagerCount ?? (props.responsive && isPhone.value ? 5 : 7))

function applyInputLabels() {
  const root = paginationRef.value?.$el
  root?.querySelector<HTMLInputElement>('.el-pagination__sizes input')?.setAttribute(
    'aria-label',
    'Rows per page',
  )
  root?.querySelector<HTMLInputElement>('.el-pagination__jump input')?.setAttribute(
    'aria-label',
    'Page',
  )
}

onMounted(() => void nextTick(applyInputLabels))
watch(
  () => [props.currentPage, props.pageSize, props.total, isPhone.value],
  () => void nextTick(applyInputLabels),
)
</script>

<template>
  <ElPagination
    ref="paginationRef"
    v-bind="$attrs"
    class="hfl-pagination"
    :current-page="currentPage"
    :page-size="pageSize"
    :total="total"
    :page-sizes="pageSizes"
    :layout="resolvedLayout"
    :size="size"
    :popper-class="mergedPopperClass"
    :pager-count="resolvedPagerCount"
    @update:current-page="emit('update:currentPage', $event)"
    @update:page-size="emit('update:pageSize', $event)"
  />
</template>

<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronDown, Pencil, Plus, Trash2 } from 'lucide-vue-next'

withDefaults(
  defineProps<{
    addLabel?: string
    addDisabled?: boolean
    editDisabled?: boolean
    deleteDisabled?: boolean
  }>(),
  {
    addLabel: '',
    addDisabled: false,
    editDisabled: true,
    deleteDisabled: true,
  },
)

const emit = defineEmits<{
  add: []
  edit: []
  delete: []
}>()

const { t } = useI18n()
const moreActionsOpen = ref(false)
</script>

<template>
  <el-button
    type="primary"
    :disabled="addDisabled"
    @click="emit('add')"
  >
    <Plus :size="16" />
    {{ addLabel || t('platformOps.list.btnAdd') }}
  </el-button>

  <el-dropdown
    trigger="click"
    popper-class="hfl-actions-dropdown"
    @visible-change="moreActionsOpen = $event"
  >
    <el-button>
      {{ t('platformOps.list.btnMoreActions') }}
      <ChevronDown
        :size="16"
        class="hfl-list-more__chev"
        :class="{ 'hfl-list-more__chev--open': moreActionsOpen }"
      />
    </el-button>
    <template #dropdown>
      <el-dropdown-menu>
        <el-dropdown-item
          :disabled="editDisabled"
          @click="emit('edit')"
        >
          <span class="el-dropdown-menu__item-content">
            <Pencil
              :size="14"
              class="shrink-0"
            />
            <span>{{ t('common.edit') }}</span>
          </span>
        </el-dropdown-item>
        <el-dropdown-item
          divided
          class="el-dropdown-menu__item--danger"
          :disabled="deleteDisabled"
          @click="emit('delete')"
        >
          <span class="el-dropdown-menu__item-content">
            <Trash2
              :size="14"
              class="shrink-0"
            />
            <span>{{ t('common.delete') }}</span>
          </span>
        </el-dropdown-item>
      </el-dropdown-menu>
    </template>
  </el-dropdown>
</template>

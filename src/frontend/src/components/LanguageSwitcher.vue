<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { Check, ChevronDown, Globe } from 'lucide-vue-next'
import { useLocaleSwitch } from '../composables/useLocaleSwitch'

const props = withDefaults(
  defineProps<{
    variant?: 'auth' | 'navigation' | 'mobile'
  }>(),
  { variant: 'navigation' },
)

const emit = defineEmits<{
  change: [locale: string]
}>()

const { t, locale } = useI18n()
const {
  canSwitchLocale,
  currentLocaleLabel,
  localeOptions,
  selectLocale,
} = useLocaleSwitch()
const dropdownOpen = ref(false)

const popperClass = computed(
  () => `hfl-language-switcher-popper hfl-language-switcher-popper--${props.variant}`,
)
const ariaLabel = computed(
  () => `${t('nav.languageLabel')}: ${currentLocaleLabel.value}`,
)

function handleCommand(code: string | number | object) {
  if (typeof code !== 'string') return
  if (!selectLocale(code)) return
  emit('change', code)
}

function handleVisibleChange(visible: boolean) {
  dropdownOpen.value = visible
}
</script>

<template>
  <ElDropdown
    v-if="canSwitchLocale"
    :class="['language-switcher', `language-switcher--${variant}`]"
    trigger="click"
    placement="bottom-end"
    :popper-class="popperClass"
    :hide-on-click="true"
    @visible-change="handleVisibleChange"
    @command="handleCommand"
  >
    <button
      type="button"
      class="language-switcher__trigger"
      :aria-label="ariaLabel"
      aria-haspopup="menu"
      :aria-expanded="dropdownOpen"
    >
      <Globe
        class="language-switcher__globe"
        :size="variant === 'mobile' ? 18 : 17"
        aria-hidden="true"
      />
      <span
        v-if="variant === 'mobile'"
        class="language-switcher__mobile-copy"
      >
        <span class="language-switcher__mobile-title">{{ t('nav.languageLabel') }}</span>
        <span class="language-switcher__mobile-current">{{ currentLocaleLabel }}</span>
      </span>
      <span
        v-else
        class="language-switcher__current"
      >{{ currentLocaleLabel }}</span>
      <ChevronDown
        :class="['language-switcher__chevron', { 'is-open': dropdownOpen }]"
        :size="15"
        aria-hidden="true"
      />
    </button>

    <template #dropdown>
      <ElDropdownMenu
        role="menu"
        :aria-label="t('nav.languageLabel')"
      >
        <ElDropdownItem
          v-for="option in localeOptions"
          :key="option.code"
          :command="option.code"
          :class="{ 'is-selected': option.code === String(locale) }"
          :aria-current="option.code === String(locale) ? 'true' : undefined"
        >
          <span class="language-switcher__option">
            <span class="language-switcher__option-label">{{ option.label }}</span>
            <Check
              v-if="option.code === String(locale)"
              class="language-switcher__check"
              :size="16"
              aria-hidden="true"
            />
          </span>
        </ElDropdownItem>
      </ElDropdownMenu>
    </template>
  </ElDropdown>
  <span
    v-else
    :class="['language-switcher', 'language-switcher--static', `language-switcher--${variant}`]"
    :aria-label="ariaLabel"
  >
    <Globe
      class="language-switcher__globe"
      :size="variant === 'mobile' ? 18 : 17"
      aria-hidden="true"
    />
    <span
      v-if="variant === 'mobile'"
      class="language-switcher__mobile-copy"
    >
      <span class="language-switcher__mobile-title">{{ t('nav.languageLabel') }}</span>
      <span class="language-switcher__mobile-current">{{ currentLocaleLabel }}</span>
    </span>
    <span
      v-else
      class="language-switcher__current"
    >{{ currentLocaleLabel }}</span>
  </span>
</template>

<style scoped>
.language-switcher {
  display: inline-flex;
  flex: 0 0 auto;
}

.language-switcher__trigger,
.language-switcher--static {
  display: inline-flex;
  align-items: center;
  min-height: 34px;
  gap: 7px;
  padding: 5px 9px;
  color: inherit;
  font: inherit;
  line-height: 1;
  white-space: nowrap;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 8px;
}

.language-switcher__trigger {
  flex: 0 0 auto;
  cursor: pointer;
  transition: background-color 0.15s ease, border-color 0.15s ease;
}

.language-switcher__trigger:hover,
.language-switcher__trigger:focus-visible {
  background: rgba(255, 255, 255, 0.08);
  border-color: rgba(255, 255, 255, 0.16);
}

.language-switcher__trigger:focus-visible {
  outline: 2px solid rgba(174, 164, 255, 0.9);
  outline-offset: 2px;
}

.language-switcher__trigger:focus:not(:focus-visible) {
  outline: none;
}

.language-switcher__globe {
  flex: 0 0 auto;
  opacity: 0.86;
}

.language-switcher__current {
  font-size: 13px;
  font-weight: 500;
  line-height: 1.4;
}

.language-switcher__chevron {
  flex: 0 0 auto;
  opacity: 0.7;
  transition: transform 0.15s ease;
}

.language-switcher__chevron.is-open {
  transform: rotate(180deg);
}

.language-switcher--auth {
  color: rgba(255, 255, 255, 0.9);
}

.language-switcher--navigation {
  color: var(--nav-text-secondary, rgba(255, 255, 255, 0.78));
}

.language-switcher--navigation .language-switcher__trigger,
.language-switcher--navigation.language-switcher--static {
  min-height: 32px;
  padding: 5px 9px;
  background: var(--tz-bg, rgba(255, 255, 255, 0.045));
  border-color: var(--tz-border, rgba(255, 255, 255, 0.1));
}

.language-switcher--navigation .language-switcher__trigger:hover,
.language-switcher--navigation .language-switcher__trigger:focus-visible {
  background: var(--icon-btn-hover-bg, rgba(255, 255, 255, 0.08));
  border-color: rgba(255, 255, 255, 0.18);
}

.language-switcher--mobile {
  width: 100%;
}

.language-switcher--mobile .language-switcher__trigger,
.language-switcher--mobile.language-switcher--static {
  width: 100%;
  justify-content: flex-start;
  min-height: 44px;
  padding: 8px 12px;
}

.language-switcher__mobile-copy {
  display: flex;
  min-width: 0;
  flex: 1;
  flex-direction: column;
  gap: 3px;
  text-align: left;
}

.language-switcher__mobile-title {
  font-size: 13px;
  font-weight: 600;
  line-height: 1.25;
}

.language-switcher__mobile-current {
  overflow: hidden;
  color: var(--color-text-tertiary, #909399);
  font-size: 12px;
  line-height: 1.25;
  text-overflow: ellipsis;
}

.language-switcher--static {
  cursor: default;
}

@media (max-width: 540px) {
  .language-switcher--auth .language-switcher__trigger {
    padding-inline: 7px;
  }
}
</style>

<style>
.hfl-language-switcher-popper.el-popper {
  min-width: 180px;
  padding: 6px !important;
  background: #272633 !important;
  border: 1px solid rgba(255, 255, 255, 0.14) !important;
  border-radius: 10px !important;
  box-shadow: 0 12px 32px rgba(0, 0, 0, 0.34) !important;
}

.hfl-language-switcher-popper .el-dropdown-menu {
  padding: 0 !important;
  background: transparent !important;
}

.hfl-language-switcher-popper.el-popper .el-popper__arrow::before {
  background: #272633 !important;
  border-color: rgba(255, 255, 255, 0.14) !important;
}

.hfl-language-switcher-popper .el-dropdown-menu__item {
  min-height: 36px;
  padding: 8px 10px !important;
  color: rgba(255, 255, 255, 0.82) !important;
  border-radius: 7px;
}

.hfl-language-switcher-popper .el-dropdown-menu__item:hover,
.hfl-language-switcher-popper .el-dropdown-menu__item:focus,
.hfl-language-switcher-popper .el-dropdown-menu__item.is-selected {
  color: #fff !important;
  background: rgba(109, 94, 246, 0.22) !important;
}

.hfl-language-switcher-popper .el-dropdown-menu__item:focus-visible {
  outline: 2px solid rgba(174, 164, 255, 0.9);
  outline-offset: -2px;
}

.language-switcher__option {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
  gap: 22px;
}

.language-switcher__option-label {
  font-size: 13px;
  line-height: 1.35;
}

.language-switcher__check {
  flex: 0 0 auto;
  color: #b9b0ff;
}

</style>

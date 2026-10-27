<script setup lang="ts">
/**
 * DingTalk wing mark (line) — path from MingCute Icon `dingtalk-line`, Apache-2.0.
 * https://github.com/Richard9394/MingCute
 */
withDefaults(
  defineProps<{
    size?: number | string
  }>(),
  { size: 24 },
)
</script>

<template>
  <svg
    xmlns="http://www.w3.org/2000/svg"
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="currentColor"
    aria-hidden="true"
  >
    <path
      fill-rule="evenodd"
      d="M6.802 2.02a1 1 0 0 1 .849.22l9.751 8.359a2 2 0 0 1 .235 2.799l-1.06 1.272l.87.436a1 1 0 0 1 .134 1.708l-7 5a1 1 0 0 1-1.539-1.101l1.21-4.034c-2.363-.9-3.747-3.055-4.233-5.483A1 1 0 0 1 7.01 10c-.474-.703-.86-1.42-1.134-2.149c-.649-1.73-.658-3.523.23-5.298a1 1 0 0 1 .696-.533m1.78 10.562c.653 1.279 1.705 2.156 3.114 2.437a1 1 0 0 1 .762 1.268l-.66 2.2l3.27-2.335c-.442-.221-.934-.406-1.047-.947a1 1 0 0 1 .21-.845l1.87-2.242L7.47 4.72c-.136.798-.032 1.6.279 2.429c.49 1.309 1.504 2.69 2.958 4.144a1 1 0 0 1-1.023 1.656z"
    />
  </svg>
</template>

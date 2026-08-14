<script setup lang="ts">
import type { EnrollmentOs } from '../../lib/nodeApi'

defineOptions({
  inheritAttrs: false,
})

defineProps<{
  os: EnrollmentOs
}>()
</script>

<template>
  <svg
    v-if="os === 'linux'"
    v-bind="$attrs"
    class="agent-platform-brand-icon agent-platform-brand-icon--linux"
    viewBox="0 0 512 512"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <g transform="matrix(2 0 0 2 256 256)">
      <path
        d="M-32-25c-3 7-24 29-22 51 8 92 36 30 78 53 0 0 75-42 15-110-17-24-2-43-13-59s-30-17-44-2 6 37-14 67"
        fill="#1e293b"
      />
      <path
        d="M42 21s9-18-8-31c16 17 6 32 6 32h-3C36-13 27 6 14-56 29-73 0-88 0-60h-9c1-24-20-12-8 5-1 37-23 52-23 78-7-18 6-32 6-32s-18 15-7 37 31 17 17 27c22 15 56 5 55-27 1-8 22-5 24-3s-3-4-13-4m-56-78c-7-2-5-11-2-11s8 7 2 11m19 1c-5-7-1-14 4-13s5 13-4 13"
        fill="#ffffff"
      />
      <g
        fill="#fabf24"
        stroke="#1e293b"
        stroke-width="1.2"
        stroke-linejoin="round"
        stroke-linecap="round"
      >
        <path
          d="M-41 31l21 30c11 7 5 35-25 21-17-5-31-4-33-13s4-10 3-14c-4-22 14-11 19-22s5-16 15-2M71 45c-4-6 0-17-14-16-6 12-23 24-24 0-10 0-3 24-7 35-9 27 17 29 28 16l26-18c2-3 5-6-9-17m-92-92c-3-6 11-14 16-14s12 4 19 6 4 9 2 10S3-35-5-35s-10-8-16-12"
        />
        <path d="M-21-48c8 6 17 11 35-3" />
      </g>
      <path
        d="M-10-54c-2 0 1-2 2-1m7 1c1-1-1-2-3-1"
        fill="#1e293b"
      />
    </g>
  </svg>

  <!-- Windows Logo -->
  <svg
    v-else-if="os === 'windows'"
    v-bind="$attrs"
    class="agent-platform-brand-icon agent-platform-brand-icon--windows"
    viewBox="0 0 120 120"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M16 26 L52 21 L52 56 L16 56 Z"
      fill="#00adef"
    />
    <path
      d="M16 61 L52 61 L52 96 L16 91 Z"
      fill="#00adef"
    />
    <path
      d="M57 20 L104 14 L104 56 L57 56 Z"
      fill="#0078d7"
    />
    <path
      d="M57 61 L104 61 L104 103 L57 97 Z"
      fill="#0078d7"
    />
  </svg>

  <!-- Apple Logo -->
  <svg
    v-else
    v-bind="$attrs"
    class="agent-platform-brand-icon agent-platform-brand-icon--macos"
    viewBox="0 0 120 120"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M86.8 63.8 C86.5 53 95.3 47.7 95.7 47.4 C90.5 40 82.5 39 79.8 38.8 C73.1 38.2 66.8 42.8 63.4 42.8 C60 42.8 54.8 38.9 49.3 39 C42.1 39.1 35.5 43.1 31.8 49.4 C24.3 62 29.9 80.7 37.1 91 C40.6 96 44.7 101.5 50.1 101.3 C55.3 101.1 57.3 98 63.5 98 C69.7 98 71.5 101.3 77 101.2 C82.5 101.1 86 96.1 89.5 91 C93.5 85.2 95.1 79.6 95.2 79.3 C95.1 79.2 87 76.1 86.8 63.8 Z"
      fill="#4B5563"
    />
    <path
      d="M78.6 28 C81.5 24.5 83.5 19.7 82.9 14.8 C78.7 15 73.6 17.6 70.6 21.1 C68 24.1 65.7 29 66.4 33.8 C71.1 34.2 75.8 31.5 78.6 28 Z"
      fill="#4B5563"
    />
  </svg>
</template>

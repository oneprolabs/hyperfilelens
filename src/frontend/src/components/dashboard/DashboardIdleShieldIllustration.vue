<script setup lang="ts">
const shieldFace =
  'M36 12.5 C46.2 12.5 51.5 17.2 51.5 23.8 L51.5 29.2 C51.5 38.4 45.4 45.8 36 49.8 C26.6 45.8 20.5 38.4 20.5 29.2 L20.5 23.8 C20.5 17.2 25.8 12.5 36 12.5 Z'

const shieldSide =
  'M36 16 C45.4 16 50.2 20.2 50.2 26.2 L50.2 31.2 C50.2 39.6 44.6 46.4 36 50.2 C27.4 46.4 21.8 39.6 21.8 31.2 L21.8 26.2 C21.8 20.2 26.6 16 36 16 Z'
</script>

<template>
  <svg
    class="dashboard-idle-shield"
    viewBox="0 0 72 72"
    width="72"
    height="72"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <defs>
      <radialGradient
        id="dashboardIdleShieldShadow"
        cx="50%"
        cy="50%"
        r="50%"
      >
        <stop
          offset="0%"
          stop-color="#818cf8"
          stop-opacity="0.26"
        />
        <stop
          offset="100%"
          stop-color="#818cf8"
          stop-opacity="0"
        />
      </radialGradient>
      <linearGradient
        id="dashboardIdleShieldTop"
        x1="24"
        y1="14"
        x2="48"
        y2="48"
        gradientUnits="userSpaceOnUse"
      >
        <stop stop-color="#ffffff" />
        <stop
          offset="0.45"
          stop-color="#eef2ff"
        />
        <stop
          offset="1"
          stop-color="#e7ebf3"
        />
      </linearGradient>
      <linearGradient
        id="dashboardIdleShieldSide"
        x1="24"
        y1="18"
        x2="48"
        y2="52"
        gradientUnits="userSpaceOnUse"
      >
        <stop stop-color="#c8d0df" />
        <stop
          offset="1"
          stop-color="#9aa6bc"
        />
      </linearGradient>
      <linearGradient
        id="dashboardIdleShieldTint"
        x1="28"
        y1="16"
        x2="44"
        y2="42"
        gradientUnits="userSpaceOnUse"
      >
        <stop
          stop-color="#818cf8"
          stop-opacity="0.18"
        />
        <stop
          offset="1"
          stop-color="#a5b4fc"
          stop-opacity="0.08"
        />
      </linearGradient>
      <linearGradient
        id="dashboardIdleShieldHighlight"
        x1="30"
        y1="18"
        x2="38"
        y2="30"
        gradientUnits="userSpaceOnUse"
      >
        <stop
          stop-color="#ffffff"
          stop-opacity="0.85"
        />
        <stop
          offset="1"
          stop-color="#ffffff"
          stop-opacity="0"
        />
      </linearGradient>
      <filter
        id="dashboardIdleShieldGlow"
        x="-30%"
        y="-20%"
        width="160%"
        height="170%"
      >
        <feDropShadow
          dx="0"
          dy="5"
          stdDeviation="4.5"
          flood-color="#7c6cf6"
          flood-opacity="0.18"
        />
      </filter>
    </defs>

    <ellipse
      cx="36"
      cy="57"
      rx="18"
      ry="5"
      fill="url(#dashboardIdleShieldShadow)"
    />

    <g opacity="0.92">
      <path
        :d="shieldSide"
        fill="url(#dashboardIdleShieldSide)"
      />
    </g>

    <g filter="url(#dashboardIdleShieldGlow)">
      <path
        :d="shieldFace"
        fill="url(#dashboardIdleShieldTop)"
        stroke="#cbd5e1"
        stroke-width="0.75"
      />
      <path
        :d="shieldFace"
        fill="url(#dashboardIdleShieldTint)"
      />
      <path
        d="M36 18.5 C41.8 18.5 45.2 21.6 45.2 25.8 L45.2 29.2 C45.2 35.2 41.2 40.2 36 42.8 C30.8 40.2 26.8 35.2 26.8 29.2 L26.8 25.8 C26.8 21.6 30.2 18.5 36 18.5 Z"
        fill="url(#dashboardIdleShieldHighlight)"
      />
      <path
        d="M33.2 30.8 L35.4 33.2 L39.4 27.8"
        stroke="#94a3b8"
        stroke-width="1.35"
        stroke-linecap="round"
        stroke-linejoin="round"
        opacity="0.55"
      />
    </g>
  </svg>
</template>

<style scoped>
.dashboard-idle-shield {
  display: block;
}
</style>

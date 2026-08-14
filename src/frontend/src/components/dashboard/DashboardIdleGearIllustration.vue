<script setup lang="ts">
function gearPath(cx: number, cy: number, teeth: number, outerR: number, innerR: number) {
  const parts: string[] = []
  for (let i = 0; i < teeth * 2; i += 1) {
    const angle = (Math.PI * 2 * i) / (teeth * 2) - Math.PI / 2
    const radius = i % 2 === 0 ? outerR : innerR
    const x = cx + radius * Math.cos(angle)
    const y = cy + radius * Math.sin(angle)
    parts.push(`${i === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`)
  }
  return `${parts.join(' ')} Z`
}

const gearBody = gearPath(36, 31, 8, 18.5, 14)
const gearEdge = gearPath(36, 34.5, 8, 18.5, 14)
</script>

<template>
  <svg
    class="dashboard-idle-gear"
    viewBox="0 0 72 72"
    width="72"
    height="72"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <defs>
      <radialGradient
        id="dashboardIdleGearShadow"
        cx="50%"
        cy="50%"
        r="50%"
      >
        <stop
          offset="0%"
          stop-color="#8b7cf8"
          stop-opacity="0.24"
        />
        <stop
          offset="100%"
          stop-color="#8b7cf8"
          stop-opacity="0"
        />
      </radialGradient>
      <linearGradient
        id="dashboardIdleGearTop"
        x1="22"
        y1="16"
        x2="50"
        y2="48"
        gradientUnits="userSpaceOnUse"
      >
        <stop stop-color="#ffffff" />
        <stop
          offset="1"
          stop-color="#e7ebf3"
        />
      </linearGradient>
      <linearGradient
        id="dashboardIdleGearSide"
        x1="22"
        y1="20"
        x2="50"
        y2="52"
        gradientUnits="userSpaceOnUse"
      >
        <stop stop-color="#c8d0df" />
        <stop
          offset="1"
          stop-color="#a7b2c6"
        />
      </linearGradient>
      <linearGradient
        id="dashboardIdleGearHub"
        x1="32"
        y1="26"
        x2="40"
        y2="36"
        gradientUnits="userSpaceOnUse"
      >
        <stop stop-color="#f8fafc" />
        <stop
          offset="1"
          stop-color="#e2e8f0"
        />
      </linearGradient>
      <filter
        id="dashboardIdleGearGlow"
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
          flood-opacity="0.16"
        />
      </filter>
    </defs>

    <ellipse
      cx="36"
      cy="57"
      rx="19"
      ry="5"
      fill="url(#dashboardIdleGearShadow)"
    />

    <g opacity="0.92">
      <path
        :d="gearEdge"
        fill="url(#dashboardIdleGearSide)"
      />
      <circle
        cx="36"
        cy="34.5"
        r="6.8"
        fill="#b8c2d3"
      />
    </g>

    <g filter="url(#dashboardIdleGearGlow)">
      <path
        :d="gearBody"
        fill="url(#dashboardIdleGearTop)"
        stroke="#d5dce8"
        stroke-width="0.75"
      />
      <circle
        cx="36"
        cy="31"
        r="6.8"
        fill="url(#dashboardIdleGearHub)"
        stroke="#d5dce8"
        stroke-width="0.75"
      />
      <circle
        cx="36"
        cy="31"
        r="2.6"
        fill="#dbe2ec"
      />
    </g>
  </svg>
</template>

<style scoped>
.dashboard-idle-gear {
  display: block;
}
</style>

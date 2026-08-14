<script setup lang="ts">
defineProps<{
  direct: boolean
  sourceLabels: string[]
}>()
</script>

<template>
  <div
    class="nas-proxy-topology"
    :class="{ 'nas-proxy-topology--direct': direct }"
    aria-hidden="true"
  >
    <template v-if="!direct">
      <div class="nas-proxy-topology__sources">
        <span
          v-for="label in sourceLabels"
          :key="label"
          class="nas-proxy-topology__source"
        >
          {{ label }}
        </span>
      </div>
      <div class="nas-proxy-topology__merge">
        <span class="nas-proxy-topology__merge-line nas-proxy-topology__merge-line--top" />
        <span class="nas-proxy-topology__merge-line nas-proxy-topology__merge-line--middle" />
        <span class="nas-proxy-topology__merge-line nas-proxy-topology__merge-line--bottom" />
      </div>
      <div class="nas-proxy-topology__node nas-proxy-topology__node--proxy">
        Proxy
      </div>
      <div class="nas-proxy-topology__arrow" />
      <div class="nas-proxy-topology__node nas-proxy-topology__node--nas">
        NAS
      </div>
    </template>

    <template v-else>
      <div class="nas-proxy-topology__direct-rows">
        <div
          v-for="label in sourceLabels"
          :key="label"
          class="nas-proxy-topology__direct-row"
        >
          <span class="nas-proxy-topology__source">{{ label }}</span>
          <span class="nas-proxy-topology__arrow nas-proxy-topology__arrow--direct" />
          <span class="nas-proxy-topology__node nas-proxy-topology__node--nas">NAS</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.nas-proxy-topology {
  display: grid;
  grid-template-columns: 74px 42px 88px 34px 72px;
  align-items: center;
  min-height: 150px;
  padding: 18px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 8px;
  background: linear-gradient(180deg, rgba(255, 255, 255, 0.95) 0%, rgba(248, 250, 252, 0.9) 100%);
}

.nas-proxy-topology--direct {
  display: flex;
  align-items: center;
  padding: 16px 18px;
  border-color: rgba(226, 232, 240, 0.95);
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.94) 0%, rgba(241, 245, 249, 0.72) 100%);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.82);
}

.nas-proxy-topology__sources,
.nas-proxy-topology__direct-rows {
  display: grid;
  gap: 10px;
}

.nas-proxy-topology__source,
.nas-proxy-topology__node {
  display: inline-flex;
  align-items: center;
  justify-content: center;
}

.nas-proxy-topology__source {
  height: 28px;
  min-width: 72px;
  border: 1px solid rgba(203, 213, 225, 0.95);
  border-radius: 6px;
  background: #fff;
  color: rgb(51 65 85);
  font-size: 12px;
  font-weight: 600;
}

.nas-proxy-topology__merge {
  position: relative;
  height: 96px;
}

.nas-proxy-topology__merge::before {
  content: '';
  position: absolute;
  top: 14px;
  bottom: 14px;
  left: 12px;
  width: 1px;
  background: rgb(148 163 184);
}

.nas-proxy-topology__merge::after {
  content: '';
  position: absolute;
  top: 50%;
  left: 12px;
  right: 0;
  height: 1px;
  background: rgb(148 163 184);
}

.nas-proxy-topology__merge-line {
  display: none;
}

.nas-proxy-topology__node {
  height: 38px;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 700;
}

.nas-proxy-topology__node--proxy {
  border: 1px solid rgba(37, 99, 235, 0.28);
  background: rgba(239, 246, 255, 0.95);
  color: rgb(29 78 216);
}

.nas-proxy-topology__node--nas {
  border: 1px solid rgba(22, 163, 74, 0.28);
  background: linear-gradient(180deg, rgba(240, 253, 244, 0.98), rgba(220, 252, 231, 0.86));
  color: rgb(21 128 61);
}

.nas-proxy-topology__arrow {
  position: relative;
  height: 1px;
  background: rgb(148 163 184);
}

.nas-proxy-topology__arrow::after {
  display: none;
}

.nas-proxy-topology__direct-rows {
  width: 100%;
  gap: 12px;
  min-width: 0;
}

.nas-proxy-topology__direct-row {
  display: grid;
  grid-template-columns: 82px minmax(72px, 1fr) 82px;
  align-items: center;
  gap: 12px;
}

.nas-proxy-topology__arrow--direct {
  height: 2px;
  min-width: 72px;
  border-radius: 999px;
  background: linear-gradient(90deg, rgba(148, 163, 184, 0.28), rgba(69, 122, 176, 0.62), rgba(22, 163, 74, 0.42));
}

.nas-proxy-topology__arrow--direct::before {
  content: '';
  position: absolute;
  top: 50%;
  left: 0;
  width: 5px;
  height: 5px;
  border-radius: 999px;
  background: rgba(148, 163, 184, 0.5);
  transform: translateY(-50%);
}

.nas-proxy-topology__arrow--direct::after {
  content: '';
  display: block;
  position: absolute;
  top: 50%;
  right: -1px;
  width: 6px;
  height: 6px;
  border-top: 1px solid rgba(22, 163, 74, 0.72);
  border-right: 1px solid rgba(22, 163, 74, 0.72);
  transform: translateY(-50%) rotate(45deg);
}

.nas-proxy-topology--direct .nas-proxy-topology__source,
.nas-proxy-topology--direct .nas-proxy-topology__node {
  width: 100%;
  height: 32px;
  border-radius: 6px;
  box-sizing: border-box;
}

@media (max-width: 640px) {
  .nas-proxy-topology {
    grid-template-columns: 68px 34px 80px 28px 64px;
    padding: 14px;
    overflow-x: auto;
  }

  .nas-proxy-topology--direct {
    display: flex;
  }

  .nas-proxy-topology__direct-row {
    grid-template-columns: 78px minmax(64px, 1fr) 78px;
  }

  .nas-proxy-topology__arrow--direct {
    min-width: 64px;
  }
}
</style>

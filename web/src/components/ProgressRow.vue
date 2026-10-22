<script setup>
import { computed } from 'vue'
import { formatBytes, formatSpeed } from '../lib/util'

const props = defineProps({
  name: { type: String, required: true },
  size: { type: Number, required: true },
  value: { type: Number, default: 0 },
  status: { type: String, default: 'idle' },
  speed: { type: Number, default: 0 },
  sha256: { type: String, default: '' },
  verified: { type: Boolean, default: false },
})

const percent = computed(() => {
  if (!props.size) return 0
  return Math.min(100, Math.round((props.value / props.size) * 100))
})

const label = computed(() => {
  if (props.status === 'done') return '已完成'
  if (props.status === 'error') return '校验失败'
  if (props.status === 'hashing') return '正在计算校验值'
  if (props.status === 'uploaded') return '已上传到服务器'
  return `${percent.value}%`
})
</script>

<template>
  <div class="file-row">
    <div class="spread">
      <span class="name truncate" :title="name">{{ name }}</span>
      <span class="size muted">{{ formatBytes(size) }}</span>
    </div>
    <div class="bar">
      <div
        class="fill"
        :class="{ done: status === 'done' || status === 'uploaded', error: status === 'error' }"
        :style="{ width: percent + '%' }"
      ></div>
    </div>
    <div class="spread meta">
      <span class="muted">{{ label }}</span>
      <span class="muted">
        <template v-if="status === 'hashing'">SHA-256 计算中</template>
        <template v-else-if="status === 'done' && verified">SHA-256 一致</template>
        <template v-else-if="status === 'error'">SHA-256 不一致</template>
        <template v-else-if="speed > 0">{{ formatSpeed(speed) }}</template>
      </span>
    </div>
  </div>
</template>

<style scoped>
.file-row {
  display: flex;
  flex-direction: column;
  gap: 7px;
}

.name {
  font-size: 14px;
  font-weight: 500;
  max-width: 70%;
}

.size {
  font-size: 12px;
  white-space: nowrap;
}

.bar {
  height: 6px;
  border-radius: 999px;
  background: var(--sky-100);
  overflow: hidden;
}

.fill {
  height: 100%;
  background: var(--sky-400);
  transition: width 0.18s linear;
}

.fill.done {
  background: var(--success);
}

.fill.error {
  background: var(--danger);
}

.meta {
  font-size: 12px;
}
</style>

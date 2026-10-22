<script setup>
import { ref } from 'vue'
import { createApi } from '../lib/api'

const props = defineProps({
  baseUrl: { type: String, required: true },
})
const emit = defineEmits(['close', 'save'])

const value = ref(props.baseUrl)
const testing = ref(false)
const result = ref('')

async function test() {
  testing.value = true
  result.value = ''
  try {
    const cfg = await createApi(value.value.trim()).config()
    result.value = `连接正常，单文件上限 ${Math.round(cfg.max_file_size / 1024 / 1024)} MB`
  } catch (error) {
    result.value = `连接失败：${error.message}`
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="panel card stack">
      <div class="spread">
        <h2>设置</h2>
        <button class="btn-ghost" @click="emit('close')">关闭</button>
      </div>

      <label class="stack" style="gap: 6px">
        <span class="muted">服务器地址</span>
        <input v-model="value" placeholder="http://192.168.1.10:8791" autocapitalize="off" spellcheck="false" />
      </label>

      <p class="muted">
        网页版一般无需修改；安装成安卓应用后，这里要填服务器的访问地址。
      </p>

      <div class="row">
        <button class="btn-secondary" :disabled="testing" @click="test">
          {{ testing ? '测试中…' : '测试连接' }}
        </button>
        <button class="btn-primary" @click="emit('save', value)">保存</button>
      </div>

      <p v-if="result" class="muted">{{ result }}</p>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: grid;
  place-items: center;
  padding: 16px;
  z-index: 50;
}

.panel {
  width: 100%;
  max-width: 420px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.18);
}
</style>

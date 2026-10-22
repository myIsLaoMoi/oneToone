<script setup>
import { computed, onMounted, ref } from 'vue'
import { createApi } from './lib/api'
import { formatBytes } from './lib/util'
import SendView from './components/SendView.vue'
import ReceiveView from './components/ReceiveView.vue'
import SettingsView from './components/SettingsView.vue'

const BASE_KEY = 'one-to-one:base-url'

const baseUrl = ref('')
const view = ref('home')
const route = ref({})
const showSettings = ref(false)
const bootError = ref('')

const hasBase = computed(() => Boolean(baseUrl.value))

function readRoute() {
  const hash = window.location.hash.replace(/^#/, '')
  const [path, query] = hash.split('?')
  const params = new URLSearchParams(query || '')
  if (path === '/send') return { name: 'send', token: params.get('t') || '', key: params.get('k') || '' }
  if (path === '/receive') {
    return { name: 'receive', code: params.get('c') || '', token: params.get('t') || '', key: params.get('k') || '' }
  }
  return { name: 'home' }
}

function go(name, params = {}) {
  const query = new URLSearchParams(params).toString()
  const hash = name === 'home' ? '' : `#/${name}${query ? `?${query}` : ''}`
  if (window.location.hash === hash) {
    route.value = readRoute()
    view.value = route.value.name
    return
  }
  window.location.hash = hash
}

function onHashChange() {
  route.value = readRoute()
  view.value = route.value.name
}

function saveBase(value) {
  const cleaned = value.trim().replace(/\/+$/, '')
  baseUrl.value = cleaned
  localStorage.setItem(BASE_KEY, cleaned)
  showSettings.value = false
}

onMounted(async () => {
  const saved = localStorage.getItem(BASE_KEY)
  baseUrl.value = saved || window.location.origin
  if (!saved) localStorage.setItem(BASE_KEY, baseUrl.value)

  window.addEventListener('hashchange', onHashChange)
  onHashChange()

  try {
    await createApi(baseUrl.value).config()
  } catch {
    bootError.value = `无法连接服务器 ${baseUrl.value}，请检查地址或在设置中修改。`
  }
})
</script>

<template>
  <header class="spread header">
    <div class="brand" @click="go('home')">
      <span class="logo">⇄</span>
      <h1>文件快传</h1>
    </div>
    <button class="btn-ghost" @click="showSettings = true" title="设置">设置</button>
  </header>

  <div v-if="bootError" class="alert error" style="margin-bottom: 16px">{{ bootError }}</div>

  <main>
    <section v-if="view === 'home'" class="stack">
      <div class="card hero">
        <h2>两台设备，直接互传</h2>
        <p class="muted" style="margin-top: 6px">
          优先走点对点直连，文件不经过服务器；直连不通时自动改用服务器中转，断线可续传。
        </p>
      </div>

      <div class="choices">
        <button class="choice" @click="go('send')">
          <span class="choice-icon">↑</span>
          <span class="choice-title">发送文件</span>
          <span class="muted">选好文件后生成 6 位验证码</span>
        </button>
        <button class="choice" @click="go('receive')">
          <span class="choice-icon">↓</span>
          <span class="choice-title">接收文件</span>
          <span class="muted">输入对方给你的验证码</span>
        </button>
      </div>

      <div class="card">
        <h2 style="margin-bottom: 8px">怎么用</h2>
        <ol class="steps muted">
          <li>发送方选择文件，得到 6 位验证码和一条链接。</li>
          <li>把验证码或链接发给对方（微信、短信都行）。</li>
          <li>接收方输入验证码，双方页面都打开时优先直连。</li>
          <li>直连失败会自动转为服务器中转，对方离线也能稍后再取。</li>
        </ol>
      </div>
    </section>

    <SendView
      v-else-if="view === 'send'"
      :base-url="baseUrl"
      :token="route.token"
      :access-key="route.key"
      @navigate="go"
    />

    <ReceiveView v-else-if="view === 'receive'" :base-url="baseUrl" :initial-code="route.code" @navigate="go" />

    <SettingsView v-if="showSettings" :base-url="baseUrl" @close="showSettings = false" @save="saveBase" />
  </main>
</template>

<style scoped>
.header {
  margin-bottom: 18px;
}

.brand {
  display: flex;
  align-items: center;
  gap: 9px;
  cursor: pointer;
}

.logo {
  width: 30px;
  height: 30px;
  border-radius: var(--radius);
  background: var(--sky-500);
  color: #fff;
  display: grid;
  place-items: center;
  font-size: 17px;
}

.hero h2 {
  font-size: 18px;
}

.choices {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
}

.choice {
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  gap: 4px;
  padding: 18px;
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  text-align: left;
}

.choice:hover {
  border-color: var(--sky-400);
  background: var(--sky-50);
}

.choice-icon {
  width: 32px;
  height: 32px;
  border-radius: var(--radius);
  background: var(--sky-100);
  color: var(--sky-600);
  display: grid;
  place-items: center;
  font-size: 17px;
  margin-bottom: 4px;
}

.choice-title {
  font-weight: 600;
}

.steps {
  margin: 0;
  padding-left: 20px;
}

.steps li {
  margin-bottom: 5px;
}

@media (max-width: 480px) {
  .choices {
    grid-template-columns: 1fr;
  }
}
</style>

<script setup>
import { computed, onBeforeUnmount, reactive, ref, watch } from 'vue'
import { createApi } from '../lib/api'
import { hashFile } from '../lib/hash'
import { TransferSession } from '../lib/session'
import { copyText, formatBytes } from '../lib/util'
import ProgressRow from './ProgressRow.vue'

const props = defineProps({
  baseUrl: { type: String, required: true },
  token: { type: String, default: '' },
  accessKey: { type: String, default: '' },
})
const emit = defineEmits(['navigate'])

const state = reactive({
  phase: 'pick', // pick | preparing | ready | transferring | done | error
  mode: 'auto', // auto | relay
  transport: 'pending',
  message: '',
  error: '',
  code: '',
  token: '',
  senderKey: '',
  joined: false,
  peerPresent: false,
  files: [],
  expiresAt: 0,
  copied: '',
})

const input = ref(null)
const dragOver = ref(false)
const now = ref(Date.now())
let clock = null
let session = null

const totalBytes = computed(() => state.files.reduce((sum, f) => sum + f.size, 0))
const sentBytes = computed(() => state.files.reduce((sum, f) => sum + (f.value || 0), 0))
const overall = computed(() => (totalBytes.value ? Math.round((sentBytes.value / totalBytes.value) * 100) : 0))
const remaining = computed(() => {
  if (!state.expiresAt) return ''
  const ms = state.expiresAt * 1000 - now.value
  if (ms <= 0) return '已过期'
  const hours = Math.floor(ms / 3600000)
  const minutes = Math.floor((ms % 3600000) / 60000)
  return hours > 0 ? `${hours} 小时 ${minutes} 分` : `${minutes} 分钟`
})

const shareLink = computed(() => {
  if (!state.code) return ''
  // Inside the Android shell the page origin is http://localhost, which is
  // useless to the recipient; the configured server address is the real host.
  const base = props.baseUrl.replace(/\/+$/, '')
  return `${base}/#/receive?c=${state.code}`
})

watch(
  () => state.phase,
  (value) => {
    if (value === 'ready' || value === 'transferring') {
      if (!clock) clock = setInterval(() => (now.value = Date.now()), 30000)
    }
  },
)

onBeforeUnmount(() => {
  if (clock) clearInterval(clock)
  if (session) session.close()
})

function pick() {
  input.value && input.value.click()
}

function onFiles(event) {
  const list = [...(event.target.files || [])]
  event.target.value = ''
  addFiles(list)
}

function onDrop(event) {
  dragOver.value = false
  addFiles([...(event.dataTransfer.files || [])])
}

function addFiles(list) {
  if (!list.length) return
  for (const file of list) {
    state.files.push({
      file,
      name: file.name,
      size: file.size,
      mime: file.type,
      value: 0,
      status: 'idle',
      sha256: '',
      verified: false,
      speed: 0,
      lastAt: 0,
      lastValue: 0,
    })
  }
  state.error = ''
}

function removeFile(index) {
  state.files.splice(index, 1)
}

function trackSpeed(entry, value) {
  const t = Date.now()
  if (entry.lastAt && t > entry.lastAt) {
    const delta = value - entry.lastValue
    const seconds = (t - entry.lastAt) / 1000
    if (delta >= 0 && seconds > 0) entry.speed = delta / seconds
  }
  entry.lastAt = t
  entry.lastValue = value
}

async function start() {
  if (!state.files.length) return
  state.error = ''
  state.phase = 'preparing'
  state.message = '正在计算文件校验值…'

  try {
    // Hashing up front means the receiver can verify what it got, and lets us
    // show a real progress bar instead of a frozen "uploading" state.
    for (const entry of state.files) {
      entry.status = 'hashing'
      entry.sha256 = await hashFile(entry.file, (value) => {
        entry.value = value
        trackSpeed(entry, value)
      })
      entry.status = 'idle'
      entry.value = 0
    }

    state.message = '正在创建传输…'
    const api = createApi(props.baseUrl)
    const created = await api.createTransfer(
      state.files.map((f) => ({ name: f.name, size: f.size, mime: f.mime })),
    )

    state.token = created.token
    state.senderKey = created.sender_key
    state.code = created.code
    state.expiresAt = created.expires_at
    state.phase = 'ready'
    state.message = '等待对方输入验证码…'

    await api.submitHashes(
      created.token,
      created.sender_key,
      state.files.map((f, idx) => ({ idx, sha256: f.sha256 })),
    )

    history.replaceState(null, '', `#/send?t=${created.token}&k=${created.sender_key}`)

    session = new TransferSession({
      baseUrl: props.baseUrl,
      role: 'sender',
      token: created.token,
      key: created.sender_key,
      files: state.files.map((f) => ({ file: f.file, name: f.name, size: f.size, mime: f.mime })),
      iceServers: created.stun.map((urls) => ({ urls })),
      p2pChunkSize: created.p2p_chunk_size,
      relayChunkSize: created.relay_chunk_size,
      timeoutMs: created.p2p_timeout_ms,
      forceRelay: state.mode === 'relay',
      handlers: {
        onState: (payload) => {
          state.transport = payload.transport
          state.message = payload.message
          if (payload.transport === 'relay' && state.phase === 'ready') state.phase = 'transferring'
          if (payload.message === '传输完成') state.phase = 'done'
        },
        onProgress: ({ idx, value }) => {
          const entry = state.files[idx]
          if (!entry) return
          entry.value = value
          trackSpeed(entry, value)
          if (state.phase === 'ready' && value > 0) state.phase = 'transferring'
          if (state.transport === 'relay' && value >= entry.size) entry.status = 'uploaded'
        },
        onPeer: ({ type }) => {
          if (type === 'present') {
            state.peerPresent = true
            state.joined = true
          }
          if (type === 'left') state.peerPresent = false
          if (type === 'ws-closed' && state.phase !== 'done') {
            state.message = '与服务器的连接中断，正在重连…'
          }
        },
        onFileVerified: (idx, sha256) => {
          const entry = state.files[idx]
          if (!entry) return
          entry.verified = entry.sha256 === sha256
          entry.status = entry.verified ? 'done' : 'error'
        },
        onFileSent: (idx) => {
          const entry = state.files[idx]
          if (entry) entry.status = 'done'
        },
        onAllSent: () => {
          state.phase = 'done'
          state.message = '传输完成'
        },
        onRelayReady: () => {
          if (state.transport === 'relay') {
            state.files.forEach((f) => {
              if (f.value >= f.size) f.status = 'uploaded'
            })
          }
        },
        onError: (message) => {
          state.error = message
          state.phase = 'error'
        },
        hasPeer: () => state.peerPresent,
      },
    })
    await session.start()
  } catch (error) {
    state.error = error.message || '创建传输失败'
    state.phase = 'error'
  }
}

async function copy(value, label) {
  const ok = await copyText(value)
  state.copied = ok ? label : '复制失败，请手动选择'
  setTimeout(() => (state.copied = ''), 2000)
}

function cancel() {
  if (session) session.close()
  if (state.token) createApi(props.baseUrl).cancel(state.token, state.senderKey).catch(() => {})
  emit('navigate', 'home')
}

function reset() {
  if (session) session.close()
  session = null
  state.phase = 'pick'
  state.files = []
  state.token = ''
  state.code = ''
  state.senderKey = ''
  state.error = ''
  state.peerPresent = false
  state.transport = 'pending'
}
</script>

<template>
  <div class="stack">
    <!-- Step 1: choose files -->
    <template v-if="state.phase === 'pick'">
      <div
        class="dropzone"
        :class="{ active: dragOver }"
        @click="pick"
        @dragover.prevent="dragOver = true"
        @dragleave.prevent="dragOver = false"
        @drop.prevent="onDrop"
      >
        <span class="drop-icon">＋</span>
        <strong>选择文件</strong>
        <span class="muted">点击选择，或把文件拖到这里</span>
      </div>
      <input ref="input" type="file" multiple hidden @change="onFiles" />

      <div v-if="state.files.length" class="card stack">
        <div class="spread">
          <h2>{{ state.files.length }} 个文件</h2>
          <span class="muted">{{ formatBytes(totalBytes) }}</span>
        </div>
        <div v-for="(entry, index) in state.files" :key="index" class="spread file-item">
          <span class="truncate">{{ entry.name }}</span>
          <div class="row">
            <span class="muted">{{ formatBytes(entry.size) }}</span>
            <button class="btn-ghost" @click="removeFile(index)">移除</button>
          </div>
        </div>

        <div class="stack" style="gap: 8px">
          <span class="muted">传输方式</span>
          <label class="option" :class="{ selected: state.mode === 'auto' }">
            <input v-model="state.mode" type="radio" value="auto" />
            <span>
              <strong>优先直连</strong>
              <span class="muted"> 双方都在线时不经过服务器；不通则自动改用服务器中转</span>
            </span>
          </label>
          <label class="option" :class="{ selected: state.mode === 'relay' }">
            <input v-model="state.mode" type="radio" value="relay" />
            <span>
              <strong>先传到服务器</strong>
              <span class="muted"> 对方不必在线，稍后用验证码取</span>
            </span>
          </label>
        </div>

        <button class="btn-primary" @click="start">开始传输</button>
      </div>

      <div v-if="state.error" class="alert error">{{ state.error }}</div>
    </template>

    <!-- Step 2: preparing -->
    <template v-else-if="state.phase === 'preparing'">
      <div class="card stack">
        <h2>准备中</h2>
        <p class="muted">{{ state.message }}</p>
        <ProgressRow
          v-for="(entry, index) in state.files"
          :key="index"
          :name="entry.name"
          :size="entry.size"
          :value="entry.value"
          :status="entry.status"
          :speed="entry.speed"
        />
      </div>
    </template>

    <!-- Step 3: ready / transferring / done -->
    <template v-else>
      <div class="card stack">
        <div class="spread">
          <h2>{{ state.phase === 'done' ? '传输完成' : '等待接收' }}</h2>
          <span class="badge" :class="{ ok: state.transport === 'p2p', warn: state.transport === 'relay' }">
            {{ state.transport === 'p2p' ? '点对点直连' : state.transport === 'relay' ? '服务器中转' : '协商中' }}
          </span>
        </div>

        <div class="code-box" @click="copy(state.code, '验证码已复制')">
          <span class="code-label muted">验证码</span>
          <span class="code mono">{{ state.code }}</span>
          <span class="muted">点击复制</span>
        </div>

        <div class="stack" style="gap: 8px">
          <span class="muted">或发送这条链接给对方，点开即可接收</span>
          <div class="link-box">
            <span class="mono truncate">{{ shareLink }}</span>
            <button class="btn-secondary" @click="copy(shareLink, '链接已复制')">复制</button>
          </div>
        </div>

        <p v-if="state.copied" class="muted">{{ state.copied }}</p>

        <div class="alert info">
          <div class="spread">
            <span>{{ state.message }}</span>
            <span class="muted" v-if="remaining">有效期 {{ remaining }}</span>
          </div>
          <p class="muted" style="margin-top: 4px">
            {{ state.peerPresent ? '对方已连接。' : '对方输入验证码后，这里会自动开始传输。' }}
          </p>
        </div>
      </div>

      <div class="card stack">
        <div class="spread">
          <h2>进度</h2>
          <span class="muted">{{ overall }}% · {{ formatBytes(sentBytes) }} / {{ formatBytes(totalBytes) }}</span>
        </div>
        <div class="bar-total">
          <div class="fill-total" :style="{ width: overall + '%' }"></div>
        </div>
        <ProgressRow
          v-for="(entry, index) in state.files"
          :key="index"
          :name="entry.name"
          :size="entry.size"
          :value="entry.value"
          :status="entry.status"
          :speed="entry.speed"
          :sha256="entry.sha256"
          :verified="entry.verified"
        />
      </div>

      <div v-if="state.error" class="alert error">{{ state.error }}</div>

      <div class="row">
        <button v-if="state.phase === 'done'" class="btn-primary" @click="reset">再传一批</button>
        <button class="btn-secondary" @click="cancel">取消并删除</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 40px 20px;
  border: 2px dashed var(--sky-200);
  border-radius: var(--radius);
  background: var(--surface);
  cursor: pointer;
  text-align: center;
  transition: border-color 0.15s ease, background 0.15s ease;
}

.dropzone:hover,
.dropzone.active {
  border-color: var(--sky-400);
  background: var(--sky-50);
}

.drop-icon {
  font-size: 26px;
  color: var(--sky-500);
  line-height: 1;
}

.file-item {
  padding: 8px 0;
  border-bottom: 1px solid var(--line);
}

.file-item:last-of-type {
  border-bottom: none;
}

.option {
  display: flex;
  align-items: flex-start;
  gap: 9px;
  padding: 10px 12px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  cursor: pointer;
  font-size: 14px;
}

.option.selected {
  border-color: var(--sky-400);
  background: var(--sky-50);
}

.option input {
  width: auto;
  margin-top: 3px;
}

.code-box {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 2px;
  padding: 18px;
  border-radius: var(--radius);
  background: var(--sky-50);
  border: 1px solid var(--sky-200);
  cursor: pointer;
}

.code-label {
  font-size: 12px;
}

.code {
  font-size: 34px;
  font-weight: 600;
  letter-spacing: 0.22em;
  color: var(--sky-700);
  line-height: 1.25;
}

.link-box {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: #fafcff;
  font-size: 12px;
}

.link-box .mono {
  flex: 1;
  min-width: 0;
}

.bar-total {
  height: 8px;
  border-radius: 999px;
  background: var(--sky-100);
  overflow: hidden;
}

.fill-total {
  height: 100%;
  background: var(--sky-500);
  transition: width 0.2s linear;
}
</style>

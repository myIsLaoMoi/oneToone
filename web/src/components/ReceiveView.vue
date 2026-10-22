<script setup>
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { createApi } from '../lib/api'
import { TransferSession } from '../lib/session'
import { formatBytes } from '../lib/util'
import ProgressRow from './ProgressRow.vue'

const props = defineProps({
  baseUrl: { type: String, required: true },
  initialCode: { type: String, default: '' },
})
const emit = defineEmits(['navigate'])

const state = reactive({
  phase: 'input', // input | connecting | transferring | done | error
  code: props.initialCode || '',
  transport: 'pending',
  message: '',
  error: '',
  token: '',
  key: '',
  files: [],
  peerPresent: false,
  relayReady: false,
})

let session = null
// The code is single-use server-side, so a duplicate join (component remount or
// a re-entered hash) would fail with "expired" after the first one succeeded.
let joined = false
let joining = false

const totalBytes = computed(() => state.files.reduce((sum, f) => sum + f.size, 0))
const gotBytes = computed(() => state.files.reduce((sum, f) => sum + (f.value || 0), 0))
const overall = computed(() => (totalBytes.value ? Math.round((gotBytes.value / totalBytes.value) * 100) : 0))
const allDone = computed(
  () => state.files.length > 0 && state.files.every((f) => f.status === 'done' || f.status === 'uploaded'),
)

onMounted(() => {
  if (props.initialCode && props.initialCode.length === 6) join()
})

// Opening a second shared link in the same tab must start over: the component is
// not remounted because the route name stays "receive".
watch(
  () => props.initialCode,
  (value, previous) => {
    if (!value || value.length !== 6 || value === previous) return
    if (joined) {
      reset()
      state.code = value
      join()
    } else {
      state.code = value
    }
  },
)

onBeforeUnmount(() => {
  if (session) session.close()
})

function normalizeCode(value) {
  return value.replace(/\D/g, '').slice(0, 6)
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

async function join() {
  const code = normalizeCode(state.code)
  if (code.length !== 6) {
    state.error = '请输入 6 位数字验证码'
    return
  }
  if (joined || joining) return
  joining = true
  state.error = ''
  state.phase = 'connecting'
  state.message = '正在查找传输…'

  try {
    const api = createApi(props.baseUrl)
    const result = await api.join(code)
    joined = true
    joining = false
    state.token = result.token
    state.key = result.receiver_key
    state.files = result.files.map((f) => ({
      name: f.name,
      size: f.size,
      mime: f.mime,
      value: 0,
      status: 'idle',
      sha256: f.sha256 || '',
      verified: false,
      speed: 0,
      lastAt: 0,
      lastValue: 0,
    }))

    history.replaceState(null, '', `#/receive?t=${result.token}&k=${result.receiver_key}`)
    state.phase = 'transferring'

    session = new TransferSession({
      baseUrl: props.baseUrl,
      role: 'receiver',
      token: result.token,
      key: result.receiver_key,
      files: state.files.map((f) => ({ name: f.name, size: f.size, mime: f.mime })),
      iceServers: result.stun.map((urls) => ({ urls })),
      p2pChunkSize: result.p2p_chunk_size,
      relayChunkSize: result.relay_chunk_size,
      timeoutMs: result.p2p_timeout_ms,
      // The sender may have already committed to the relay ("upload first");
      // trying P2P then would just burn the connection timeout.
      forceRelay: result.transport === 'relay',
      handlers: {
        onState: (payload) => {
          state.transport = payload.transport
          state.message = payload.message
          if (payload.message === '传输完成') state.phase = 'done'
        },
        onProgress: ({ idx, value }) => {
          const entry = state.files[idx]
          if (!entry) return
          entry.value = value
          trackSpeed(entry, value)
        },
        onPeer: ({ type }) => {
          if (type === 'present') state.peerPresent = true
          if (type === 'left') {
            state.peerPresent = false
            if (state.transport === 'p2p') state.message = '发送方已离线，等待其回来继续'
          }
        },
        onFileReceived: (idx, blob, digest) => {
          const entry = state.files[idx]
          if (!entry) return
          entry.blob = blob
          entry.status = 'done'
          if (entry.sha256) {
            entry.verified = entry.sha256 === digest
            if (!entry.verified) entry.status = 'error'
          }
        },
        onFileVerified: (idx, sha256) => {
          const entry = state.files[idx]
          if (!entry) return
          entry.sha256 = entry.sha256 || sha256
        },
        onRelayReady: () => {
          state.relayReady = true
        },
        onError: (message) => {
          state.error = message
        },
        hasPeer: () => state.peerPresent,
      },
    })
    await session.start()
  } catch (error) {
    joining = false
    state.phase = 'input'
    state.error = error.message || '加入失败'
  }
}

function saveFile(index) {
  const entry = state.files[index]
  if (!entry || !entry.blob) return
  const url = URL.createObjectURL(entry.blob)
  const a = document.createElement('a')
  a.href = url
  a.download = entry.name
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 60000)
}

function saveAll() {
  state.files.forEach((entry, index) => {
    if (entry.blob) setTimeout(() => saveFile(index), index * 250)
  })
}

function downloadFromServer() {
  if (session) session.downloadFromRelay()
}

function reset() {
  if (session) session.close()
  session = null
  joined = false
  joining = false
  state.phase = 'input'
  state.code = ''
  state.files = []
  state.token = ''
  state.key = ''
  state.error = ''
  state.relayReady = false
  history.replaceState(null, '', '#/receive')
}
</script>

<template>
  <div class="stack">
    <template v-if="state.phase === 'input'">
      <div class="card stack">
        <h2>输入验证码</h2>
        <p class="muted">向发送方要 6 位数字，或直接打开对方给你的链接。</p>
        <input
          v-model="state.code"
          class="code-input mono"
          inputmode="numeric"
          autocomplete="one-time-code"
          maxlength="6"
          placeholder="000000"
          @input="state.code = normalizeCode(state.code)"
          @keyup.enter="join"
        />
        <button class="btn-primary" :disabled="state.code.length !== 6" @click="join">接收</button>
      </div>
      <div v-if="state.error" class="alert error">{{ state.error }}</div>
    </template>

    <template v-else>
      <div class="card stack">
        <div class="spread">
          <h2>{{ allDone ? '接收完成' : '正在接收' }}</h2>
          <span class="badge" :class="{ ok: state.transport === 'p2p', warn: state.transport === 'relay' }">
            {{ state.transport === 'p2p' ? '点对点直连' : state.transport === 'relay' ? '服务器中转' : '协商中' }}
          </span>
        </div>
        <div class="alert info">
          <span>{{ state.message }}</span>
          <p class="muted" style="margin-top: 4px">
            {{ state.peerPresent ? '发送方在线。' : '发送方不在线时，可从服务器下载已上传的文件。' }}
          </p>
        </div>
        <div class="bar-total">
          <div class="fill-total" :style="{ width: overall + '%' }"></div>
        </div>
        <p class="muted">{{ overall }}% · {{ formatBytes(gotBytes) }} / {{ formatBytes(totalBytes) }}</p>
      </div>

      <div class="card stack">
        <h2>文件</h2>
        <div v-for="(entry, index) in state.files" :key="index" class="stack" style="gap: 6px">
          <ProgressRow
            :name="entry.name"
            :size="entry.size"
            :value="entry.value"
            :status="entry.status"
            :speed="entry.speed"
            :sha256="entry.sha256"
            :verified="entry.verified"
          />
          <div class="row">
            <button v-if="entry.blob" class="btn-secondary" @click="saveFile(index)">保存</button>
            <span v-if="entry.blob && entry.sha256 && !entry.verified" class="muted">校验值不一致，请重新接收</span>
          </div>
        </div>
      </div>

      <div v-if="state.relayReady" class="card stack">
        <h2>服务器中转</h2>
        <p class="muted">文件已在服务器上，可交给浏览器下载（支持断点续传，不占内存）。</p>
        <button class="btn-primary" @click="downloadFromServer">从服务器下载全部</button>
      </div>

      <div v-if="state.error" class="alert error">{{ state.error }}</div>

      <div class="row">
        <button v-if="allDone && state.files.some((f) => f.blob)" class="btn-primary" @click="saveAll">
          保存全部
        </button>
        <button class="btn-secondary" @click="reset">重新输入验证码</button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.code-input {
  font-size: 28px;
  letter-spacing: 0.3em;
  text-align: center;
  padding: 14px;
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

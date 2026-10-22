export const HIGH_WATER = 4 * 1024 * 1024
export const LOW_WATER = 512 * 1024

const HEADER = 8
// Browser Blobs larger than a few MB get backed by disk, so received chunks are
// periodically folded into one and the ArrayBuffers released. Without this a
// multi-hundred-MB file would be held entirely in memory.
const FOLD_THRESHOLD = 16 * 1024 * 1024

function encodeFrame(idx, offset, payload) {
  const frame = new Uint8Array(HEADER + payload.byteLength)
  const view = new DataView(frame.buffer)
  view.setUint32(0, idx, true)
  view.setUint32(4, offset, true)
  frame.set(payload, HEADER)
  return frame
}

function waitDrain(channel) {
  return new Promise((resolve) => {
    const onLow = () => {
      channel.removeEventListener('bufferedamountlow', onLow)
      resolve()
    }
    channel.bufferedAmountLowThreshold = LOW_WATER
    channel.addEventListener('bufferedamountlow', onLow)
  })
}

export class P2PSender {
  constructor({ channel, files, chunkSize, onProgress, onFileDone }) {
    this.channel = channel
    this.files = files
    this.chunkSize = chunkSize
    this.onProgress = onProgress || (() => {})
    this.onFileDone = onFileDone || (() => {})
    this.cancelled = false
  }

  cancel() {
    this.cancelled = true
  }

  async send() {
    const resume = await this._awaitResume()
    for (let idx = 0; idx < this.files.length; idx++) {
      if (this.cancelled) throw new Error('已取消')
      const entry = this.files[idx]
      const start = Math.min(resume[idx] || 0, entry.file.size)
      if (start >= entry.file.size) {
        this.onProgress(idx, entry.file.size, entry.file.size)
        continue
      }
      await this._sendFile(idx, entry, start)
    }
    this.channel.send(JSON.stringify({ t: 'all-done' }))
  }

  _awaitResume() {
    // The receiver is the authority on how many bytes it already holds; it
    // announces that the moment the channel opens, which enables resume.
    return new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.channel.removeEventListener('message', onMessage)
        resolve({})
      }, 3000)
      const onMessage = (event) => {
        if (typeof event.data !== 'string') return
        let msg
        try {
          msg = JSON.parse(event.data)
        } catch {
          return
        }
        if (msg.t !== 'hello') return
        clearTimeout(timer)
        this.channel.removeEventListener('message', onMessage)
        const map = {}
        for (const item of msg.files || []) map[item.idx] = item.received || 0
        resolve(map)
      }
      this.channel.addEventListener('message', onMessage)
    })
  }

  async _sendFile(idx, entry, start) {
    const { file } = entry
    let offset = start
    this.onProgress(idx, offset, file.size)

    while (offset < file.size) {
      if (this.cancelled) throw new Error('已取消')
      if (this.channel.readyState !== 'open') throw new Error('连接已断开')
      if (this.channel.bufferedAmount > HIGH_WATER) await waitDrain(this.channel)

      const end = Math.min(offset + this.chunkSize, file.size)
      const buffer = await file.slice(offset, end).arrayBuffer()
      this.channel.send(encodeFrame(idx, offset, new Uint8Array(buffer)))
      offset = end
      this.onProgress(idx, offset, file.size)
    }
    this.onFileDone(idx)
  }
}

export class P2PReceiver {
  constructor({ channel, files, onProgress, onFileDone, onError, resumeState }) {
    this.channel = channel
    this.files = files
    this.onProgress = onProgress || (() => {})
    this.onFileDone = onFileDone || (() => {})
    this.onError = onError || (() => {})
    this.pending = files.map(() => [])
    this.pendingBytes = files.map(() => 0)
    this.folded = resumeState ? resumeState.folded : files.map(() => [])
    this.received = resumeState ? resumeState.received : files.map(() => 0)
    this.done = resumeState ? resumeState.done : files.map(() => false)
    this.channel.binaryType = 'arraybuffer'
    this._bind()
  }

  /** Detach from the old channel and continue on a freshly negotiated one. */
  rebind(channel) {
    this.channel = channel
    this.channel.binaryType = 'arraybuffer'
    this._bind()
  }

  snapshot() {
    return { folded: this.folded, received: this.received, done: this.done }
  }

  offsets() {
    return this.received.map((value, idx) => ({ idx, received: value }))
  }

  sayHello() {
    this.channel.send(JSON.stringify({ t: 'hello', files: this.offsets() }))
  }

  _bind() {
    this.channel.addEventListener('message', (event) => {
      if (typeof event.data === 'string') {
        this._onControl(event.data)
      } else {
        this._onBinary(event.data)
      }
    })
  }

  _onControl(raw) {
    let msg
    try {
      msg = JSON.parse(raw)
    } catch {
      return
    }
    if (msg.t === 'all-done') {
      const missing = this.files.filter((_, idx) => this.received[idx] < this.files[idx].size)
      if (missing.length) this.onError(`仍有 ${missing.length} 个文件未接收完整`)
    }
  }

  _onBinary(buffer) {
    const view = new DataView(buffer)
    const idx = view.getUint32(0, true)
    const offset = view.getUint32(4, true)
    const payload = new Uint8Array(buffer, HEADER)
    const expected = this.received[idx]

    if (offset < expected) return
    if (offset !== expected) {
      // Ordered + reliable SCTP makes this unreachable; treat it as corruption
      // rather than appending a hole into the file.
      this.onError(`数据块错序：期望 ${expected}，收到 ${offset}`)
      return
    }

    this.pending[idx].push(payload)
    this.pendingBytes[idx] += payload.byteLength
    this.received[idx] = expected + payload.byteLength
    this.onProgress(idx, this.received[idx], this.files[idx].size)

    if (this.pendingBytes[idx] >= FOLD_THRESHOLD) this._fold(idx)
    if (this.received[idx] >= this.files[idx].size) this._finalize(idx)
  }

  _fold(idx) {
    const blob = new Blob(this.pending[idx], { type: 'application/octet-stream' })
    this.folded[idx].push(blob)
    this.pending[idx] = []
    this.pendingBytes[idx] = 0
  }

  async _finalize(idx) {
    if (this.done[idx]) return
    this.done[idx] = true
    if (this.pending[idx].length) this._fold(idx)

    const entry = this.files[idx]
    const blob = new Blob(this.folded[idx], { type: entry.mime || 'application/octet-stream' })
    this.folded[idx] = [blob]

    const hasher = await createSha256()
    hasher.init()
    const READ = 4 * 1024 * 1024
    let offset = 0
    while (offset < blob.size) {
      const slice = await blob.slice(offset, Math.min(offset + READ, blob.size)).arrayBuffer()
      hasher.update(new Uint8Array(slice))
      offset += slice.byteLength
    }
    const digest = hasher.digest('hex')
    this.channel.send(JSON.stringify({ t: 'file-ok', idx, sha256: digest }))
    this.onFileDone(idx, blob, digest)
  }
}

async function createSha256() {
  const { createSHA256 } = await import('hash-wasm')
  return createSHA256()
}

/**
 * Establishes a peer connection.
 * `send(payload)` posts a signaling payload to the other side over WebSocket;
 * `onRemoteMessage(handler)` delivers the other side's payloads.
 */
export async function openPeer({ iceServers, timeoutMs, isInitiator, send, onRemoteMessage, onState }) {
  const pc = new RTCPeerConnection({ iceServers })

  let data = isInitiator ? pc.createDataChannel('data', { ordered: true }) : null
  let ctrl = isInitiator ? pc.createDataChannel('ctrl', { ordered: true }) : null
  if (data) data.binaryType = 'arraybuffer'

  const ready = new Promise((resolve, reject) => {
    let settled = false
    const finish = (fn, arg) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      fn(arg)
    }
    const timer = setTimeout(() => finish(reject, new Error('P2P 连接超时')), timeoutMs)

    const check = () => {
      if (data && ctrl && data.readyState === 'open' && ctrl.readyState === 'open') {
        finish(resolve, { data, ctrl })
      }
    }

    if (!isInitiator) {
      pc.ondatachannel = (event) => {
        const ch = event.channel
        if (ch.label === 'data') {
          ch.binaryType = 'arraybuffer'
          data = ch
        } else {
          ctrl = ch
        }
        check()
      }
    } else {
      data.onopen = check
      ctrl.onopen = check
    }

    pc.onconnectionstatechange = () => {
      if (onState) onState(pc.connectionState)
      if (pc.connectionState === 'failed') finish(reject, new Error('P2P 连接失败'))
      // A local close() is handled by the session itself; only an unexpected
      // close during setup counts as a failed attempt.
    }

    pc.onicecandidate = (event) => {
      if (event.candidate) send({ kind: 'ice', candidate: event.candidate.toJSON() })
    }
  })

  const onSdp = async (msg) => {
    await pc.setRemoteDescription(new RTCSessionDescription(msg.description))
    if (msg.description.type === 'offer') {
      const answer = await pc.createAnswer()
      await pc.setLocalDescription(answer)
      send({ kind: 'sdp', description: pc.localDescription.toJSON() })
    }
  }

  onRemoteMessage(async (msg) => {
    try {
      if (msg.kind === 'sdp') {
        await onSdp(msg)
      } else if (msg.kind === 'ice') {
        await pc.addIceCandidate(new RTCIceCandidate(msg.candidate))
      }
    } catch {
      // A candidate may arrive before the remote description is applied; the
      // next one usually succeeds, and ICE retries on its own.
    }
  })

  if (isInitiator) {
    const offer = await pc.createOffer()
    await pc.setLocalDescription(offer)
    send({ kind: 'sdp', description: pc.localDescription.toJSON() })
  }

  return { pc, channels: await ready }
}

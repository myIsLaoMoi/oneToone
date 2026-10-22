import { createApi, openSignaling } from './api'
import { P2PSender, P2PReceiver, openPeer } from './p2p'
import { RelayUploader, RelayDownloader } from './relay'

const MAX_P2P_RETRY = 2

function sleep(ms) {
  return new Promise((r) => setTimeout(r, ms))
}

/**
 * Drives a single transfer end to end.
 *
 * Transport selection is deliberately optimistic: try P2P first, and only fall
 * back to the relay once the peer connection has definitively failed or the
 * timeout elapsed. A dropped P2P session is retried before giving up, because
 * server bandwidth is the scarce resource here.
 */
export class TransferSession {
  constructor({ baseUrl, role, token, key, files, iceServers, p2pChunkSize, relayChunkSize, timeoutMs, handlers, forceRelay }) {
    this.api = createApi(baseUrl)
    this.baseUrl = baseUrl
    this.role = role
    this.token = token
    this.key = key
    this.files = files
    this.iceServers = iceServers
    this.p2pChunkSize = p2pChunkSize
    this.relayChunkSize = relayChunkSize
    this.timeoutMs = timeoutMs
    this.h = handlers
    this.forceRelay = Boolean(forceRelay)
    this.transport = 'pending'
    this.closed = false
    this.ws = null
    this.peer = null
    this.sender = null
    this.receiver = null
    this.uploader = null
    this.signalQueue = []
    this.signalHandler = null
    this.attempt = 0
    this.progress = files.map(() => 0)
  }

  get isSender() {
    return this.role === 'sender'
  }

  async start() {
    this.h.onState({ transport: 'pending', message: '正在连接服务器…' })
    await this._connectSignaling()
    // "Upload first" mode must not wait for a receiver: the whole point is that
    // the other side may be offline and collect the file later.
    if (this.forceRelay) await this._fallbackToRelay('已选择先传到服务器')
  }

  close() {
    this.closed = true
    if (this.sender) this.sender.cancel()
    if (this.uploader) this.uploader.cancel()
    if (this.receiver) this.receiver.cancel && this.receiver.cancel()
    if (this.peer) {
      try {
        this.peer.close()
      } catch {
        /* already closed */
      }
    }
    if (this.ws) {
      try {
        this.ws.close()
      } catch {
        /* already closed */
      }
    }
  }

  _connectSignaling() {
    return new Promise((resolve, reject) => {
      let ws
      try {
        ws = openSignaling(this.baseUrl, this.token, this.role, this.key)
      } catch (error) {
        reject(error)
        return
      }
      this.ws = ws
      let opened = false

      ws.onopen = () => {
        opened = true
        this._startPing()
        resolve()
      }
      ws.onmessage = (event) => {
        let msg
        try {
          msg = JSON.parse(event.data)
        } catch {
          return
        }
        this._onSignalMessage(msg)
      }
      ws.onclose = (event) => {
        this._stopPing()
        if (!opened) {
          reject(new Error(event.code === 4403 ? '访问密钥无效' : '无法建立实时连接'))
          return
        }
        if (this.closed) return
        if (event.code === 4409) {
          this.h.onState({ transport: this.transport, message: '此页面已被另一个标签页接管' })
          return
        }
        this.h.onPeer({ type: 'ws-closed' })
        if (this.transport === 'p2p') this._onP2PDropped('连接中断')
      }
      ws.onerror = () => {
        if (!opened) reject(new Error('无法建立实时连接'))
      }
    })
  }

  _startPing() {
    this._stopPing()
    this.pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send(JSON.stringify({ type: 'ping' }))
      }
    }, 25000)
  }

  _stopPing() {
    if (this.pingTimer) {
      clearInterval(this.pingTimer)
      this.pingTimer = null
    }
  }

  _signal(payload) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return
    this.ws.send(JSON.stringify({ type: 'signal', data: payload }))
  }

  _onSignalMessage(msg) {
    switch (msg.type) {
      case 'peer-present':
        this.h.onPeer({ type: 'present' })
        if (!this.forceRelay) this._maybeStartP2P()
        break
      case 'peer-absent':
        this.h.onPeer({ type: 'absent' })
        break
      case 'peer-left':
        this.h.onPeer({ type: 'left' })
        if (this.transport === 'p2p') this._onP2PDropped('对方已离线')
        break
      case 'signal':
        this._deliverSignal(msg.data)
        break
      case 'p2p-failed':
        if (this.transport !== 'relay') this._fallbackToRelay('对方无法建立直连')
        break
      case 'replaced':
        this.h.onState({ transport: this.transport, message: '此页面已被另一个标签页接管' })
        break
      default:
        break
    }
  }

  _deliverSignal(data) {
    if (this.signalHandler) {
      this.signalHandler(data)
    } else {
      this.signalQueue.push(data)
    }
  }

  async _maybeStartP2P() {
    if (this.closed || this.transport !== 'pending') return
    if (this._negotiating) return
    this._negotiating = true
    this.attempt += 1
    this.h.onState({ transport: 'p2p', message: '正在尝试直连…' })

    try {
      const peer = await openPeer({
        iceServers: this.iceServers,
        timeoutMs: this.timeoutMs,
        isInitiator: this.isSender,
        send: (payload) => this._signal(payload),
        onRemoteMessage: (handler) => {
          this.signalHandler = handler
          for (const queued of this.signalQueue.splice(0)) handler(queued)
        },
        onState: (state) => {
          if (this.closed) return
          if (state === 'disconnected') {
            // Transient: ICE may recover on its own. Only give up after a grace
            // period, otherwise a brief Wi-Fi blip costs the whole transfer.
            this.h.onState({ transport: 'p2p', message: '直连不稳定，等待恢复…' })
            this._armDisconnectGrace()
          } else if (state === 'failed') {
            this._onP2PDropped('连接失败')
          } else if (state === 'connected') {
            this._clearDisconnectGrace()
          }
        },
      })
      this.peer = peer.pc
      this.transport = 'p2p'
      this.h.onState({ transport: 'p2p', message: '直连已建立，文件不经服务器' })
      // The handshake listener above stops mattering once connected; from here on
      // a lost link must be detected during the actual transfer.
      peer.pc.onconnectionstatechange = () => {
        if (this.closed) return
        const state = peer.pc.connectionState
        if (state === 'disconnected') {
          this.h.onState({ transport: 'p2p', message: '直连不稳定，等待恢复…' })
          this._armDisconnectGrace()
        } else if (state === 'failed') {
          this._onP2PDropped('连接失败')
        } else if (state === 'connected') {
          this._clearDisconnectGrace()
        }
      }
      this._runP2P(peer.channels)
    } catch (error) {
      this._onP2PFailed(error)
    } finally {
      this._negotiating = false
    }
  }

  _runP2P(channels) {
    if (this.isSender) {
      this.sender = new P2PSender({
        channel: channels.data,
        files: this.files,
        chunkSize: this.p2pChunkSize,
        onProgress: (idx, sent, total) => this._progress(idx, sent, total),
        onFileDone: (idx) => this.h.onFileSent && this.h.onFileSent(idx),
      })
      channels.ctrl.onmessage = (event) => {
        this._onControlFromReceiver(event.data)
      }
      this.sender.send().then(
        () => {
          if (!this.closed) this.h.onAllSent && this.h.onAllSent()
        },
        (error) => {
          if (this.closed) return
          this._onP2PDropped(error.message || '直连中断')
        },
      )
      this._watchChannel(channels.data)
    } else if (this.receiver) {
      // Reuse the existing receiver so bytes already on disk are not thrown away
      // when the connection is renegotiated.
      this.receiver.rebind(channels.data)
      channels.ctrl.onmessage = (event) => this._onControlFromSender(event.data)
      this.receiver.sayHello()
      this._watchChannel(channels.data)
    } else {
      const receiver = new P2PReceiver({
        channel: channels.data,
        files: this.files,
        onProgress: (idx, got, total) => this._progress(idx, got, total),
        onFileDone: (idx, blob, digest) => this.h.onFileReceived(idx, blob, digest),
        onError: (message) => this.h.onError(message),
      })
      this.receiver = receiver
      channels.ctrl.onmessage = (event) => this._onControlFromSender(event.data)
      receiver.sayHello()
      this._watchChannel(channels.data)
    }
  }

  /**
   * A closed data channel during an unfinished transfer always means "reconnect
   * and resume", never "give up": the receiver still holds every byte it has,
   * so a retry costs nothing. Only exhausting the retries falls back to relay.
   */
  _watchChannel(channel) {
    if (this.watchedChannel === channel) return
    this.watchedChannel = channel
    channel.addEventListener('close', () => {
      if (this.closed || this.transport === 'relay') return
      this._onP2PDropped('连接已断开')
    })
  }

  _onControlFromReceiver(raw) {
    let msg
    try {
      msg = JSON.parse(raw)
    } catch {
      return
    }
    if (msg.t === 'file-ok') {
      this.h.onFileVerified && this.h.onFileVerified(msg.idx, msg.sha256)
    } else if (msg.t === 'want') {
      this.h.onState({ transport: 'p2p', message: `正在补发第 ${msg.offset} 字节之后的数据` })
    }
  }

  _onControlFromSender(raw) {
    let msg
    try {
      msg = JSON.parse(raw)
    } catch {
      return
    }
    if (msg.t === 'all-done' && this.receiver) {
      this.h.onState({ transport: 'p2p', message: '传输完成' })
    }
  }

  _progress(idx, value, total) {
    this.progress[idx] = value
    this.h.onProgress({ idx, value, total, progress: this.progress })
  }

  _onP2PFailed(error) {
    if (this.closed) return
    if (this.attempt <= MAX_P2P_RETRY && this.h.hasPeer()) {
      this.h.onState({ transport: 'p2p', message: `直连失败，正在重试（${this.attempt}/${MAX_P2P_RETRY}）…` })
      setTimeout(() => this._maybeStartP2P(), 1200)
      return
    }
    this._fallbackToRelay(error.message || '无法建立直连')
  }
  _onP2PDropped(reason) {
    if (this.closed || this.transport === 'relay') return
    this._clearDisconnectGrace()
    this.transport = 'pending'
    // A drop after bytes moved is a resume, not a fresh attempt; only a drop
    // that never got anywhere should count against the retry budget.
    const moved = this.progress.some((value) => value > 0)
    if (moved) this.attempt = Math.max(this.attempt - 1, 0)
    this.h.onState({ transport: 'p2p', message: `直连中断（${reason}），正在续传…` })
    setTimeout(() => {
      if (!this.closed) this._maybeStartP2P()
    }, 800)
  }

  _armDisconnectGrace() {
    if (this.graceTimer) return
    this.graceTimer = setTimeout(() => {
      this.graceTimer = null
      this._onP2PDropped('连接中断')
    }, 8000)
  }

  _clearDisconnectGrace() {
    if (this.graceTimer) {
      clearTimeout(this.graceTimer)
      this.graceTimer = null
    }
  }

  async _fallbackToRelay(reason) {
    if (this.closed || this.transport === 'relay') return
    this.transport = 'relay'
    this.h.onState({ transport: 'relay', message: `改用服务器中转（${reason}）` })
    this._signalFailure()

    try {
      await this.api.switchRelay(this.token, this.key, reason)
    } catch {
      /* the server may already know; not fatal */
    }

    if (this.isSender) {
      const uploader = new RelayUploader({
        api: this.api,
        token: this.token,
        key: this.key,
        files: this.files,
        chunkSize: this.relayChunkSize,
        onProgress: (idx, sent, total) => this._progress(idx, sent, total),
      })
      this.uploader = uploader
      try {
        await uploader.start()
        this.h.onState({ transport: 'relay', message: '已上传到服务器，等待对方下载' })
        this.h.onRelayReady && this.h.onRelayReady()
      } catch (error) {
        if (!this.closed) this.h.onError(`上传失败：${error.message}`)
      }
    } else {
      this.h.onState({ transport: 'relay', message: '文件在服务器上，可以开始下载' })
      this.h.onRelayReady && this.h.onRelayReady()
    }
  }

  _signalFailure() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'p2p-failed' }))
    }
  }

  /** Receiver-initiated download of everything the server holds. */
  downloadFromRelay() {
    const downloader = new RelayDownloader({
      api: this.api,
      token: this.token,
      key: this.key,
      files: this.files,
      onProgress: (idx, value) => this._progress(idx, value, this.files[idx].size),
    })
    downloader.downloadAll()
    return downloader
  }
}

export { sleep }

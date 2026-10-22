/**
 * Relay transport: the file is pushed to the server in chunks and pulled back
 * with plain HTTP range requests. Both directions are resumable because the
 * server records how many bytes it already holds per file.
 */
export class RelayUploader {
  constructor({ api, token, key, files, chunkSize, onProgress }) {
    this.api = api
    this.token = token
    this.key = key
    this.files = files
    this.chunkSize = chunkSize
    this.onProgress = onProgress || (() => {})
    this.cancelled = false
    this.offsets = files.map(() => 0)
  }

  cancel() {
    this.cancelled = true
  }

  async seed(state) {
    // Resume after a page reload: trust the server's byte count, rounded down
    // to a whole chunk so a partially written chunk is re-sent rather than assumed.
    for (const item of state.files || []) {
      const aligned = Math.floor((item.received || 0) / this.chunkSize) * this.chunkSize
      this.offsets[item.idx] = Math.min(aligned, this.files[item.idx].file.size)
    }
  }

  async start() {
    for (let idx = 0; idx < this.files.length; idx++) {
      if (this.cancelled) throw new Error('已取消')
      await this._uploadOne(idx)
    }
  }

  async _uploadOne(idx) {
    const { file } = this.files[idx]
    let offset = this.offsets[idx]
    this.onProgress(idx, offset, file.size)

    while (offset < file.size) {
      if (this.cancelled) throw new Error('已取消')
      const end = Math.min(offset + this.chunkSize, file.size)
      const blob = file.slice(offset, end)
      let result
      try {
        result = await this.api.uploadChunk(this.token, this.key, idx, offset, blob)
      } catch (error) {
        if (error.status === 409) {
          // Server has fewer bytes than we thought: rewind to its count.
          const match = /已有 (\d+)/.exec(error.message)
          offset = match ? Number(match[1]) : 0
          this.onProgress(idx, offset, file.size)
          continue
        }
        throw error
      }
      offset = result.received
      this.offsets[idx] = offset
      this.onProgress(idx, offset, file.size)
    }
  }
}

export class RelayDownloader {
  constructor({ api, token, key, files, onProgress }) {
    this.api = api
    this.token = token
    this.key = key
    this.files = files
    this.onProgress = onProgress || (() => {})
    this.cancelled = false
  }

  cancel() {
    this.cancelled = true
  }

  /**
   * Hands every file to the browser's download manager instead of buffering it
   * in JS. A 2GB file would otherwise exhaust the WebView's memory, and the
   * native downloader already supports range-based resume.
   */
  downloadAll() {
    this.files.forEach((entry, idx) => {
      const url = this.api.downloadUrl(this.token, this.key, idx)
      const a = document.createElement('a')
      a.href = url
      a.download = entry.name
      a.rel = 'noopener'
      document.body.appendChild(a)
      a.click()
      a.remove()
    })
  }
}

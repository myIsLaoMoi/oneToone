import { createSHA256 } from 'hash-wasm'

const CHUNK = 4 * 1024 * 1024

// Hashing runs in a worker so a 2GB file never has to be materialised in memory
// and so the UI thread keeps painting progress. crypto.subtle is deliberately
// avoided: it is unavailable on plain-HTTP origins and buffers whole inputs.
self.onmessage = async (event) => {
  const { id, file, onProgress } = event.data
  try {
    const hasher = await createSHA256()
    hasher.init()
    let offset = 0
    while (offset < file.size) {
      const end = Math.min(offset + CHUNK, file.size)
      const buffer = await file.slice(offset, end).arrayBuffer()
      hasher.update(new Uint8Array(buffer))
      offset = end
      self.postMessage({ id, type: 'progress', value: offset, total: file.size })
    }
    const digest = hasher.digest('hex')
    self.postMessage({ id, type: 'done', value: digest })
  } catch (error) {
    self.postMessage({ id, type: 'error', message: String((error && error.message) || error) })
  }
}

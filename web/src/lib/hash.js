let worker = null
let seq = 0
const pending = new Map()

function ensureWorker() {
  if (worker) return worker
  worker = new Worker(new URL('./hashWorker.js', import.meta.url), { type: 'module' })
  worker.onmessage = (event) => {
    const { id, type } = event.data
    const task = pending.get(id)
    if (!task) return
    if (type === 'progress') {
      task.onProgress(event.data.value, event.data.total)
    } else if (type === 'done') {
      pending.delete(id)
      task.resolve(event.data.value)
    } else if (type === 'error') {
      pending.delete(id)
      task.reject(new Error(event.data.message))
    }
  }
  worker.onerror = (event) => {
    for (const [id, task] of pending) {
      task.reject(new Error(event.message || '哈希计算失败'))
      pending.delete(id)
    }
  }
  return worker
}

export function hashFile(file, onProgress) {
  return new Promise((resolve, reject) => {
    const id = ++seq
    pending.set(id, { resolve, reject, onProgress: onProgress || (() => {}) })
    ensureWorker().postMessage({ id, file })
  })
}
